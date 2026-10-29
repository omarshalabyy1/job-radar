"""tailor: for the best-graded jobs (score 8+, in ai_places, no application row yet, nothing restricting them) Claude
(settings.yaml claude.model) drafts a CV, a cover letter, notes (with STAR stories) and form answers from the CV it picked, then a second call
reviews them against the job and the CV, into output/applications/<day>/<company>-<title>/: cv.md, cv.html and its
one-page A4 PDF, cover-letter.md, cover-letter.html and its PDF, notes.md, forms.md. It marks the job saved in the tracker ("draft ready <day>"), for you to finish in /job-radar.
It sends nothing and applies to nothing. With features.tailor off, no ANTHROPIC_API_KEY, no CV or no credit left it
prints why and stops green (a refused call costs nothing; the next run tries again). A job whose call fails or times
out is skipped and tried again next run. A restriction counts only with features.eligibility.

Two checks, in code: a CV, cover letter or form answer that names a gap ("I have not", "no experience" ...) is thrown away and tried
again next run; a CV PDF that is not one page, lost its links or has a heading an ATS cannot read leaves the files for you to look at, is not marked
ready, and turns the task red. tailor_job(conn, job_id) drafts one job you name (in /job-radar)."""

from __future__ import annotations

import html
import io
import os
import re
from datetime import date
from pathlib import Path

from pydantic import BaseModel

from . import claude
from .config import FEATURES, OUTPUT, SETTINGS
from .sources import time_left

TAILOR_SECONDS = 300  # the DAG gives the task 300 s; a new draft (two calls) starts only with 120 s left
MIN_SCORE = 8
MAX_TOKENS = 8000  # what the drafts need, and the reviewed copy of them
# Claude's own text with any of these names a gap (whole words, so "Slack" is fine; a bare "gap" is not here because a
# CV can name a "price gap": only a skills or experience gap is a gap in the candidate)
GAP_WORDS = re.compile(r"\b(?:not yet|haven['’]t|have not|(?:no|limited) (?:prior |direct |hands-on |professional )?experience"
                       r"|lack\w*|unfamiliar|(?:skills?|experience) gaps?)\b", re.I)

RULES = """You tailor one candidate's CV for one job and write the cover letter and notes that go with it. \
The candidate's CV is in <cv> and the job in <job>. Never invent an employer, job title, date, degree, \
certification, skill, client, number or shipped service: every one must be in <cv> (work <cv> calls "building" stays \
"building"). The CV and the cover letter never mention a gap or something the candidate has not done ("I have not yet \
worked with" is banned); gaps go only in notes_md.

Data engineering comes first, for an AI job too: the summary, the skills and each role's bullets lead with the pipelines, \
warehouses and data quality, and the AI work reads as the layer built on that data. The candidate does not train models: \
never write fine-tuning, LoRA, QLoRA, PEFT, SFT, "trained a model" or any training claim, even when the posting asks for it.

Voice, in all three: plain, direct, confident and senior, never junior or job-seeking. Lead with strengths, impact and \
achievements, with numbers only from <cv>. Short sentences. Verbs and outcomes, not adjectives. The employer is the \
subject: "your team", "your data". Spell out an abbreviation the first time a business reader meets it. Never write \
passionate, aspiring, excited, eager, thrilled, "grateful for the opportunity", unlock, leverage, seamless, cutting-edge, \
robust, delve, dynamic, synergy or "in today's fast-paced world". No AI tells: no "it's not X, it's Y" or "not just X but \
Y", no lists of three for rhythm, no dashes joining clauses, no sentence trailing into extra comma clauses, no staged \
opener, no one-line closer, no bold labels, no emoji, no praise of the company, no claim the CV cannot back.

The CV: tailor it to the posting's own duties, in its words where <cv> backs them, so the candidate reads as a fit. \
title_line: "Role | Specialty" for this job. contact: the contact items of <cv>'s header in order, each with its link \
(mailto: for the email, "" for the location and phone); the location is "Rehab, Cairo, Egypt", LinkedIn is the \
text "LinkedIn" linked to https://www.linkedin.com/in/omarshalaby1, GitHub is "github.com/omarshalabyy1" linked to \
https://github.com/omarshalabyy1 and the portfolio is the text "Portfolio: omarlabs.dev" linked to https://omarlabs.dev. summary: 4-5 lines in this \
pattern, role first, tool-dense, no headline figures: "<Title> with <N> years of experience building <what> in <stack>, \
backed by <background> that lets me <scope>. Proven track record <three concrete capabilities>, <how it is delivered \
and with whom>. Because I <X>, <benefit>." skills: 4-6 groups ordered by the posting's needs, each a label and a comma \
list. experience and projects: each entry's bullets lead with the achievement and the business outcome, then the method; \
3-5 bullets a role, 2 a project. Show work with stakeholders and turning business requirements into solutions. The \
backed outcomes to draw on, only where <cv> has them: at Bosch, the monthly reporting cycle cut from 3 days to under \
2 hours, one agreed KPI definition across inventory, procurement and finance, reconciliation so every reported figure \
matched the source; at Null Labs, a hands-off daily feed, short payments matched to the cent, one working list to \
recover, prevent or accept sorted by deadline, repeat causes by distribution center, item and carrier. Fixed facts \
that beat <cv>: "AI & Data Engineer" at "Null Labs | Florida, United States (Remote)", Apr 2026 – Present; "BI \
Developer" at "Bosch Car Service | Cairo, Egypt (Hybrid)", Apr 2025 – Mar 2026 (1 yr); "Operations Engineer" at \
"Bosch Car Service | Cairo, Egypt (On-site)", Oct 2024 – Mar 2025 (6 mos). A role's "when" is its dates with the \
length in brackets. A project's org is its context; repo_url its GitHub repo when <cv> gives one. The whole CV fits \
one A4 page: about 520 words at most, so cut the weakest bullets and projects first. file_tag: DE, BI, DA or AI, the \
kind of role.

cover_letter_md: 200-250 words, short paragraphs of 2-3 sentences, plain text with a blank line between paragraphs. \
Open with what the candidate can do for this employer's main need, not with who they are. Then 2-3 facts from the CV, \
with their numbers, that prove it. End with one clear ask: a short call about the role. Sign with the candidate's name \
only.

notes_md: the score and why, the gaps between the job and the CV and how to address each, then three likely \
interview questions with answers drawn only from the CV (same plain style as the letter), then "STAR stories": three \
stories from the CV that answer this job's main needs, each as Situation, Task, Action and Result in one short line \
each, with numbers only from the CV.

form_answers_md: ready answers to the questions application forms ask, for this job: why this company, why this role, \
a short summary of the candidate, then any question the posting itself asks. Same voice and limits as the letter, 2-4 \
sentences each, as "**Question**" then the answer. Salary, notice period, work authorization and other personal facts \
come only from <answers> when it is given, else write "see application-answers.md"."""

REVIEW_RULES = """You review one tailored application before it is sent. <cv> is the candidate's real CV, <job> the \
posting and <draft> the tailored CV, cover letter, notes and form answers, as JSON. Return the draft corrected: remove \
any employer, title, date, degree, skill, client or number not in <cv>; use the posting's own keywords wherever <cv> \
backs them, so an applicant tracking system (ATS) finds them; keep every rule the draft was written under (one A4 page, \
about 520 words, no gap named in the CV, the letter or the form answers, the same voice). Change nothing that is \
already right. changes: one short line per change you made. missing_keywords: the posting's key skills and tools the \
CV still does not show, because <cv> does not back them. The rules the draft was written under follow; their fixed \
facts and backed outcomes count as in <cv>, so keep them.

"""

CV_CSS = """@page { size: A4; margin: 8mm 12mm; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: Calibri, Carlito, "Segoe UI", Arial, sans-serif; font-size: 9.6pt; line-height: 1.2; color: #000;
       font-variant-ligatures: none; font-feature-settings: "liga" 0, "clig" 0; }
header { text-align: center; margin-bottom: 9pt; }
h1 { font-size: 21pt; font-weight: 700; color: #1F3864; line-height: 1.1; }
.title { font-size: 12pt; color: #7F7F7F; margin-top: 2pt; }
.contact { font-size: 9pt; margin-top: 2pt; white-space: nowrap; }
h2 { font-size: 11pt; font-weight: 700; color: #1F3864; letter-spacing: 0.9pt; text-transform: uppercase;
     border-bottom: 1.2pt solid #1F3864; padding-bottom: 1.5pt; margin: 7pt 0 3.5pt; }
p { margin-bottom: 2pt; }
.skill { margin-bottom: 2.5pt; }
.role { display: flex; justify-content: space-between; gap: 10pt; margin-top: 4pt; }
.grey, .when { color: #7F7F7F; }
.when { white-space: nowrap; }
ul { margin: 1.5pt 0 0 17pt; }
li { margin-bottom: 1.2pt; padding-left: 1pt; }
.tech { font-style: italic; margin-top: 1.5pt; }
a { color: inherit; text-decoration: none; }
.block { break-inside: avoid; }"""

LETTER_CSS = """@page { size: A4; margin: 22mm 22mm; }
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: Calibri, Carlito, "Segoe UI", Arial, sans-serif; font-size: 11pt; line-height: 1.35; color: #000;
       font-variant-ligatures: none; font-feature-settings: "liga" 0, "clig" 0; }
h1 { font-size: 18pt; color: #1F3864; margin-bottom: 2pt; }
.contact { font-size: 9.5pt; color: #444; border-bottom: 1.2pt solid #1F3864; padding-bottom: 6pt; margin-bottom: 16pt; }
p { margin-bottom: 10pt; }
a { color: inherit; text-decoration: none; }"""


class Link(BaseModel):
    text: str
    url: str  # "" for plain text


class SkillGroup(BaseModel):
    label: str
    items: str


class Entry(BaseModel):
    title: str
    org: str  # the grey part: "Company | City" for a role, the context for a project
    when: str
    bullets: list[str]
    technologies: str  # a project's; "" for a role
    repo_url: str  # a project's GitHub repo, else ""


class Education(BaseModel):
    degree: str
    school: str
    when: str
    note: str  # one line under it, else ""


class Draft(BaseModel):
    name: str
    title_line: str
    contact: list[Link]
    summary: str
    skills: list[SkillGroup]
    experience: list[Entry]
    projects: list[Entry]
    education: list[Education]
    certifications: list[str]
    languages: str
    file_tag: str
    cover_letter_md: str
    notes_md: str
    form_answers_md: str


class Review(BaseModel):
    draft: Draft
    changes: list[str]
    missing_keywords: list[str]


def draft_day(note: str | None) -> str | None:
    """The day of a "draft ready <day>" note (tailor writes it; your mailbox may add "; from your mailbox ..."), or None."""
    m = re.match(r"draft ready (\d{4}-\d{2}-\d{2})", note or "")
    return m.group(1) if m else None


def slug(company: str | None, title: str | None) -> str:
    """The file name of a company and title: lowercase, anything but letters and digits a dash, 80 characters."""
    return re.sub(r"[^a-z0-9]+", "-", f"{company or ''}-{title or ''}".lower()).strip("-")[:80].strip("-")


def draft_folder(day: str, company: str, title: str, job_id: int) -> Path:
    """output/applications/<day>/<company>-<title>/."""
    return OUTPUT / "applications" / day / (slug(company, title) or f"job-{job_id}")


def link(item: Link) -> str:
    return f'<a href="{html.escape(item.url)}">{html.escape(item.text)}</a>' if item.url else html.escape(item.text)


def contact_line(d: Draft) -> str:
    """The contact items, " • " between them; "Portfolio: omarlabs.dev" keeps its label outside the link."""
    parts = []
    for item in d.contact:
        label, sep, rest = item.text.partition(": ")
        parts.append(f"{html.escape(label)}: {link(Link(text=rest, url=item.url))}" if sep and item.url else link(item))
    return " • ".join(parts)


def cv_html(d: Draft) -> str:
    """The CV in the cv-look layout (omar-brand): one column, Calibri, navy headings with a rule."""
    e = html.escape

    def entry(x: Entry, project: bool = False) -> str:
        org = e(x.org) + (f' (<a href="{e(x.repo_url)}">GitHub</a>)' if x.repo_url else "")
        return ('<div class="block"><div class="role"><div class="what"><b>' + e(x.title) + '</b> — <span class="grey">'
                + org + '</span></div><div class="when">' + e(x.when) + "</div></div><ul>"
                + "".join(f"<li>{e(b)}</li>" for b in x.bullets) + "</ul>"
                + (f'<div class="tech"><b>Technologies:</b> {e(x.technologies)}</div>' if project and x.technologies else "") + "</div>")

    parts = [f'<header><h1>{e(d.name)}</h1><div class="title">{e(d.title_line)}</div>'
             f'<div class="contact">{contact_line(d)}</div></header>',
             f"<h2>Professional Summary</h2><p>{e(d.summary)}</p>", "<h2>Core Skills</h2>",
             *[f'<div class="skill"><b>{e(s.label)}:</b> {e(s.items)}</div>' for s in d.skills],
             "<h2>Professional Experience</h2>", *map(entry, d.experience)]
    if d.projects:
        parts += ["<h2>Projects</h2>", *[entry(x, project=True) for x in d.projects]]
    if d.education:
        parts += ["<h2>Education</h2>", *[
            f'<div class="role"><div class="what"><b>{e(x.degree)}</b> — <span class="grey">{e(x.school)}</span></div>'
            f'<div class="when">{e(x.when)}</div></div>' + (f"<ul><li>{e(x.note)}</li></ul>" if x.note else "")
            for x in d.education]]
    if d.certifications:
        parts += ["<h2>Certifications</h2><ul>", *[f"<li>{e(c)}</li>" for c in d.certifications], "</ul>"]
    if d.languages:
        parts.append(f"<h2>Additional</h2><p><b>Languages:</b> {e(d.languages)}</p>")
    return (f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><title>{e(d.name)} - CV</title>'
            f"<style>\n{CV_CSS}\n</style></head><body>\n" + "\n".join(parts) + "\n</body></html>\n")


def cv_markdown(d: Draft) -> str:
    """The same CV as markdown, for the Ready page."""
    lines = [f"# {d.name}", d.title_line, " • ".join(f"[{c.text}]({c.url})" if c.url else c.text for c in d.contact),
             "", "## Professional Summary", d.summary, "", "## Core Skills", *[f"**{s.label}:** {s.items}  " for s in d.skills]]
    for heading, entries, project in (("Professional Experience", d.experience, False), ("Projects", d.projects, True)):
        if entries:
            lines += ["", f"## {heading}"]
            for x in entries:
                lines += ["", f"**{x.title}** — {x.org}" + (f" ([GitHub]({x.repo_url}))" if x.repo_url else "") + f" | {x.when}",
                          *[f"- {b}" for b in x.bullets], *([f"*Technologies: {x.technologies}*"] if project and x.technologies else [])]
    if d.education:
        lines += ["", "## Education", *[f"**{x.degree}** — {x.school} | {x.when}" + (f"\n- {x.note}" if x.note else "")
                                        for x in d.education]]
    if d.certifications:
        lines += ["", "## Certifications", *[f"- {c}" for c in d.certifications]]
    if d.languages:
        lines += ["", "## Additional", f"**Languages:** {d.languages}"]
    return "\n".join(lines) + "\n"


def letter_html(d: Draft) -> str:
    """The cover letter on one A4 page, with the CV's name and contact line."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", d.cover_letter_md.strip()) if p.strip()]
    body = "".join("<p>" + "<br>".join(html.escape(line) for line in p.splitlines()) + "</p>" for p in paragraphs)
    return (f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><title>Cover letter</title><style>\n{LETTER_CSS}\n'
            f'</style></head><body><h1>{html.escape(d.name)}</h1><div class="contact">{contact_line(d)}</div>{body}</body></html>\n')


def pdf_of(page_html: str) -> bytes:
    """An A4 PDF of the page, printed by headless Chromium (Playwright) with the page's own @page size and margins."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(page_html, wait_until="load")
            return page.pdf(prefer_css_page_size=True, print_background=True)
        finally:
            browser.close()


def pdf_problems(pdf: bytes, page_html: str, pages: int = 1) -> list[str]:
    """What is wrong with a printed page: more than `pages` pages, a link of the page missing from the PDF's
    annotations (/Annots), so it is not clickable, or a section heading missing from the PDF's text, so an applicant
    tracking system (ATS) reading the text cannot find that section."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf))
    problems = [f"is {len(reader.pages)} pages, not {pages}"] if len(reader.pages) > pages else []
    found = {str(a.get_object().get("/A", {}).get("/URI")).rstrip("/") for p in reader.pages for a in p.get("/Annots") or []}
    missing = sorted({html.unescape(u).rstrip("/") for u in re.findall(r'href="([^"]+)"', page_html)} - found)
    problems += [f"lost its links: {', '.join(missing)}"] if missing else []
    squash = lambda s: re.sub(r"\s+", "", s).casefold()  # letter spacing and capitals change the text's spaces and case
    text = squash("".join(p.extract_text() or "" for p in reader.pages))
    unread = [html.unescape(h) for h in re.findall(r"<h2>(.*?)</h2>", page_html) if squash(html.unescape(h)) not in text]
    return problems + ([f"has no text an ATS can read for: {', '.join(unread)}"] if unread else [])


def write_draft(folder: Path, d: Draft, company: str) -> list[str]:
    """Write the drafts and their PDFs into `folder` ("<name> <Company> <TAG>.pdf" and its "Cover Letter.pdf");
    the problems of the CV's PDF (pdf_problems), empty when it is one page with every link."""
    folder.mkdir(parents=True, exist_ok=True)
    cv, letter = cv_html(d), letter_html(d)
    for name, text in (("cv.md", cv_markdown(d)), ("cv.html", cv), ("cover-letter.md", d.cover_letter_md),
                       ("cover-letter.html", letter), ("notes.md", d.notes_md), ("forms.md", d.form_answers_md)):
        (folder / name).write_text(text, encoding="utf-8")
    stem = f"{d.name} {re.sub(r'[^A-Za-z0-9 &.-]+', '', company).strip() or 'Job'} {d.file_tag.strip().upper() or 'CV'}"
    cv_pdf = pdf_of(cv)
    (folder / f"{stem}.pdf").write_bytes(cv_pdf)
    (folder / f"{stem} Cover Letter.pdf").write_bytes(pdf_of(letter))
    return pdf_problems(cv_pdf, cv)


def jobs_to_tailor(conn) -> list[tuple]:
    """The jobs to draft, best score first: graded MIN_SCORE or more, in ai_places, with no application row yet and (with
    features.eligibility) no restriction of the eligibility step's or of Claude's."""
    return conn.execute(
        "SELECT j.job_id, j.title, j.company, j.location, j.description, g.score, g.cv, g.reason"
        " FROM core.claude_grade g JOIN core.job j USING (job_id)"
        " WHERE g.score >= %s AND NOT EXISTS (SELECT 1 FROM core.application a WHERE a.job_id = j.job_id)"
        " AND j.place = ANY(%s) AND (coalesce(j.restriction, '') = '' AND coalesce(g.restriction, '') = '' OR NOT %s)"
        " ORDER BY g.score DESC, j.role_rank, j.job_id",
        (MIN_SCORE, SETTINGS.get("ai_places") or [], FEATURES["eligibility"])).fetchall()


def form_facts() -> str:
    """Your own answers to the personal questions of a form (salary, notice period ...): output/applications/
    application-answers.md, or "" when you have not written it."""
    path = OUTPUT / "applications" / "application-answers.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def ask(client, cvs: dict[str, str], job: tuple) -> Draft | None:
    """Claude's draft for one job (job_id, title, company, location, description, score, cv, reason), then a second call
    that reviews it against the job and the CV (its changes and the keywords the CV cannot back are added to notes.md),
    or None when it came back empty or named a gap in the CV, the cover letter or the form answers (printed; tried again
    next run). API errors are raised."""
    job_id, title, company, location, description, score, cv, reason = job
    # the rules, the CV and your answers are the same for every job that picked this CV: cached, so each call after the
    # first reads them at a tenth of the price
    facts = form_facts()
    context = f"<cv>\n{cvs.get(cv) or list(cvs.values())[-1]}\n</cv>" + (f"\n<answers>\n{facts}\n</answers>" if facts else "")
    posting = (f"<job>\n{title} at {company} ({location})\n" + (f"Graded {score}/10: {reason}\n" if score is not None else "")
               + f"\n{(description or 'No description.')[:6000]}\n</job>")
    r = client.messages.parse(
        model=SETTINGS["claude"]["model"], max_tokens=MAX_TOKENS, system=[
            {"type": "text", "text": RULES}, {"type": "text", "cache_control": {"type": "ephemeral"}, "text": context}],
        output_format=Draft, output_config={"effort": "medium"}, messages=[{"role": "user", "content": posting}])
    d = r.parsed_output
    if d is None or not (d.summary.strip() and d.experience and d.cover_letter_md.strip() and d.notes_md.strip()):
        print(f"tailor: job {job_id} ({company}) came back empty, left for the next run")
        return None
    r = client.messages.parse(
        model=SETTINGS["claude"]["model"], max_tokens=MAX_TOKENS, system=[
            {"type": "text", "text": REVIEW_RULES + RULES}, {"type": "text", "cache_control": {"type": "ephemeral"}, "text": context}],
        output_format=Review, output_config={"effort": "medium"},
        messages=[{"role": "user", "content": f"{posting}\n<draft>\n{d.model_dump_json()}\n</draft>"}])
    review = r.parsed_output
    if review is not None and review.draft.summary.strip() and review.draft.experience:
        d = review.draft
        d.notes_md += ("\n\n## Review\n" + "".join(f"- {c}\n" for c in review.changes or ["No change needed."])
                       + "\nKeywords the CV cannot show: " + (", ".join(review.missing_keywords) or "none") + "\n")
    if found := GAP_WORDS.search(f"{d.cover_letter_md}\n{d.form_answers_md}\n{cv_markdown(d)}"):
        print(f"WARNING tailor: job {job_id} ({company}): the CV or cover letter names a gap ({found.group(0)!r}), "
              "thrown away, tried again next run")
        return None
    return d


def tailor_job(conn, job_id: int, day: str | None = None) -> Path:
    """Draft one job you name, whatever its score or status (the tracker's row is left as it is); the folder it wrote.
    Raises when Claude gives no draft or the CV's PDF fails its checks."""
    job = conn.execute("SELECT j.job_id, j.title, j.company, j.location, j.description, g.score, g.cv, g.reason"
                       " FROM core.job j LEFT JOIN core.claude_grade g USING (job_id) WHERE j.job_id = %s", (job_id,)).fetchone()
    cvs = dict(conn.execute("SELECT label, text FROM core.cv ORDER BY uploaded_at").fetchall())
    if not (job and cvs):
        raise RuntimeError(f"tailor: no job {job_id} or no CV uploaded")
    d = ask(claude.client(), cvs, job)
    if d is None:
        raise RuntimeError(f"tailor: no usable draft for job {job_id}")
    folder = draft_folder(day or str(date.today()), job[2], job[1], job_id)
    if problems := write_draft(folder, d, job[2]):
        raise RuntimeError(f"tailor: job {job_id}: the CV PDF {'; '.join(problems)} ({folder})")
    return folder


def tailor(conn) -> None:
    if not (SETTINGS.get("features") or {}).get("tailor"):
        print("tailor: off in settings.yaml (features.tailor)")
        return
    cfg = SETTINGS["claude"]
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("tailor: no ANTHROPIC_API_KEY in .env, skipped")
        return
    cvs = dict(conn.execute("SELECT label, text FROM core.cv ORDER BY uploaded_at").fetchall())
    if not cvs:
        print("tailor: no CV uploaded in the tracker, skipped")
        return
    jobs = jobs_to_tailor(conn)  # no cap: the time check leaves the rest for the next run
    import anthropic  # for its errors; the SDK loads only in a run that calls Claude
    client = claude.client()  # a stuck call gives up inside the DAG's 300 s
    today = str(date.today())
    done, out_of_credit, failed = 0, False, []
    for job in jobs:
        job_id, title, company = job[:3]
        if time_left(TAILOR_SECONDS) < 120:
            break
        try:
            d = ask(client, cvs, job)
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:  # a timeout is a connection error
            if claude.out_of_credit(e):  # the balance ran out: every call would be refused
                out_of_credit = True
                break
            print(f"WARNING tailor: job {job_id} ({company}): {e!r}"[:300] + ", left for the next run")
            continue
        if d is None:
            continue
        folder = draft_folder(today, company, title, job_id)
        if problems := write_draft(folder, d, company):
            failed.append(f"job {job_id} ({company}): the CV PDF {'; '.join(problems)}")
            continue  # the files stay for you to look at; not marked ready
        # the files are written first: a job with a row always has its draft
        conn.execute("INSERT INTO core.application (job_id, status, note) VALUES (%s, 'saved', %s)"
                     " ON CONFLICT (job_id) DO NOTHING", (job_id, f"draft ready {today}"))
        conn.commit()
        done += 1
    print(f"tailor: {done} of {len(jobs)} jobs drafted with {cfg['model']}"
          + (" - WARNING the API credit ran out: no more drafts until you buy more" if out_of_credit else ""))
    if failed:
        raise RuntimeError("tailor: " + "; ".join(failed))
