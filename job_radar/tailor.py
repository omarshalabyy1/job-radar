"""tailor: for the best-graded jobs (score 8+, in ai_places, no application row yet, nothing restricting them) Claude
(settings.yaml claude.model) drafts a CV, a cover letter and notes from the CV it picked, into
output/applications/<day>/<company>-<title>/, and marks the job saved in the tracker ("draft ready
<day>"), for you to finish in /job-radar. It sends nothing and applies to nothing. With features.tailor
off, no ANTHROPIC_API_KEY, no CV or no credit left it prints why and stops green (a refused call costs
nothing; the next run tries again). A job whose call fails or times out is skipped and tried again next run.
A restriction counts only with features.eligibility."""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path

from pydantic import BaseModel

from . import claude
from .config import FEATURES, OUTPUT, SETTINGS
from .sources import time_left

TAILOR_SECONDS = 300  # the DAG gives the task 300 s; a new draft starts only with 60 s left
MIN_SCORE = 8
MAX_DRAFTS = 5  # a run; the rest wait for the next one
MAX_TOKENS = 6000  # what the three drafts need

RULES = """You tailor one candidate's CV for one job and write the cover letter and notes that go with it. \
The candidate's CV is in <cv> and the job in <job>. Never invent an employer, job title, date, degree, \
certification, skill, client or number: every one must be in <cv>. cv_md and cover_letter_md never mention a gap \
or something the candidate has not done ("I have not yet worked with" is banned); gaps go only in notes_md.

Voice, in all three: plain, direct, confident and senior, never junior or job-seeking. Lead with strengths, impact and \
achievements, with numbers only from <cv>. Short sentences. Verbs and outcomes, not adjectives. The employer is the \
subject: "your team", "your data". Spell out an abbreviation the first time a business reader meets it. Never write \
passionate, aspiring, excited, eager, thrilled, "grateful for the opportunity", unlock, leverage, seamless, cutting-edge, \
robust, delve, dynamic, synergy or "in today's fast-paced world". No AI tells: no "it's not X, it's Y" or "not just X but \
Y", no lists of three for rhythm, no dashes joining clauses, no sentence trailing into extra comma clauses, no staged \
opener, no one-line closer, no bold labels, no emoji, no praise of the company, no claim the CV cannot back.

cv_md: the CV rewritten for this job, in markdown. Reorder, reword and cut what is true and use the job's own words where \
the CV backs them. No photo. One to two pages. Header: the name, a "Role | Specialty" line, one contact line as in the \
CV. Then, skipping any section the CV lacks: Professional Summary (4-5 lines), Core Skills (groups: a bold label, then a \
comma list), Professional Experience, Projects, Education, Certifications, Additional (languages). Each role: bold title, \
company, city, dates from the CV, then 3-5 bullets starting with a verb and ending in the result or number the CV gives.

cover_letter_md: 200-250 words, short paragraphs of 2-3 sentences. Open with what the candidate can do for this \
employer's main need, not with who they are. Then 2-3 facts from the CV, with their numbers, that prove it. End \
with one clear ask: a short call about the role. Sign with the candidate's name only.

notes_md: the score and why, the gaps between the job and the CV and how to address each, then three likely \
interview questions with answers drawn only from the CV (same plain style as the letter)."""


class Draft(BaseModel):
    cv_md: str
    cover_letter_md: str
    notes_md: str


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


def jobs_to_tailor(conn, limit: int) -> list[tuple]:
    """The jobs to draft, best score first: graded MIN_SCORE or more, in ai_places, with no application row yet and (with
    features.eligibility) no restriction of the regex's or of Claude's."""
    return conn.execute(
        "SELECT j.job_id, j.title, j.company, j.location, j.description, g.score, g.cv, g.reason"
        " FROM core.claude_grade g JOIN core.job j USING (job_id)"
        " WHERE g.score >= %s AND NOT EXISTS (SELECT 1 FROM core.application a WHERE a.job_id = j.job_id)"
        " AND j.place = ANY(%s) AND (coalesce(j.restriction, '') = '' AND coalesce(g.restriction, '') = '' OR NOT %s)"
        " ORDER BY g.score DESC, j.role_rank, j.job_id LIMIT %s",
        (MIN_SCORE, SETTINGS.get("ai_places") or [], FEATURES["eligibility"], limit)).fetchall()


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
    newest = list(cvs.values())[-1]
    jobs = jobs_to_tailor(conn, MAX_DRAFTS)
    import anthropic  # for its errors; the SDK loads only in a run that calls Claude
    client = claude.client()  # a stuck call gives up inside the DAG's 300 s
    today = str(date.today())
    done, out_of_credit = 0, False
    for job_id, title, company, location, description, score, cv, reason in jobs:
        if time_left(TAILOR_SECONDS) < 60:
            break
        # the rules and the CV are the same for every job that picked this CV: cached, so each call after the
        # first reads them at a tenth of the price
        system = [{"type": "text", "text": RULES},
                  {"type": "text", "cache_control": {"type": "ephemeral"}, "text": f"<cv>\n{cvs.get(cv) or newest}\n</cv>"}]
        try:
            r = client.messages.parse(
                model=cfg["model"], max_tokens=MAX_TOKENS, system=system, output_format=Draft,
                output_config={"effort": "medium"},
                messages=[{"role": "user", "content": f"<job>\n{title} at {company} ({location})\n"
                                                      f"Graded {score}/10: {reason}\n\n"
                                                      f"{(description or 'No description.')[:6000]}\n</job>"}])
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:  # a timeout is a connection error
            if claude.out_of_credit(e):  # the balance ran out: every call would be refused
                out_of_credit = True
                break
            print(f"WARNING tailor: job {job_id} ({company}): {e!r}"[:300] + ", left for the next run")
            continue
        d = r.parsed_output
        if d is None or not (d.cv_md.strip() and d.cover_letter_md.strip() and d.notes_md.strip()):
            print(f"tailor: job {job_id} ({company}) came back empty, left for the next run")
            continue
        folder = draft_folder(today, company, title, job_id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "cv.md").write_text(d.cv_md, encoding="utf-8")
        (folder / "cover-letter.md").write_text(d.cover_letter_md, encoding="utf-8")
        (folder / "notes.md").write_text(d.notes_md, encoding="utf-8")
        # the files are written first: a job with a row always has its draft
        conn.execute("INSERT INTO core.application (job_id, status, note) VALUES (%s, 'saved', %s)"
                     " ON CONFLICT (job_id) DO NOTHING", (job_id, f"draft ready {today}"))
        conn.commit()
        done += 1
    print(f"tailor: {done} of {len(jobs)} jobs drafted with {cfg['model']}"
          + (" - WARNING the API credit ran out: no more drafts until you buy more" if out_of_credit else ""))
