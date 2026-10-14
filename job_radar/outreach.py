"""outreach_drafts: a short cold email to a startup's founder (or head of data or engineering) for each of its roles
Claude scored 7 or more, written to output/outreach/<company>-<title>.md. Nothing is sent: you read it, change it
and send it from your own Gmail.

A startup is a company in output/startups/startups.csv (research.py's startups job). Its contact is the first row of
data/contacts.csv for that company with side Founder (else Hiring) and an email; a startup with a 7+ role and no such
row gets no draft and is listed for the founders research (python -m job_radar.research founders), which looks on the
company's own site and in the press, never on LinkedIn, and adds the people it finds to data/contacts.csv.
Before the first draft for a startup, Claude researches it on the web (web search and fetch: what it sells and to whom,
its stage and news, what the hire says about its data) into output/outreach/briefs/<company>.md, once per startup; the
draft then pitches what Omar would do for them in their own terms. No brief, no draft: it is tried again next run.
The file is the record: a draft already there is not asked for again. Switched by features.outreach_drafts."""

from __future__ import annotations

import re

import pandas as pd

from .applications import SAFETY, VOICE, FollowUp, ask_for_draft, cv_for, draft_client, name_key
from .config import HOURS_OLD, OUTPUT, ROOT, SETTINGS  # noqa: F401  (SETTINGS: the switch, read by draft_client)
from .tailor import GAP_WORDS, slug

STARTUPS = OUTPUT / "startups" / "startups.csv"
CONTACTS = ROOT / "data" / "contacts.csv"
MIN_SCORE = 7
WORDS = 120  # the most words in a draft's body
SIDES = ("Founder", "Hiring")  # who a cold email goes to, in this order
# Omar's brand voice (omar-brand skill: identity-and-voice.md, content-rules.md) for a founder; VOICE adds the shared bans
BRAND = ("Write in Omar's brand voice. Start from the reader's problem, then the solution, then the result with a real number "
         "from <cv>, tools last. A founder wants money and time in seconds: revenue protected, cost saved, hours removed, "
         "data kept private. The reader is the subject: \"your team\", \"your data\". Plain, direct, confident and senior, a "
         "builder talking to a builder: never job-seeking, no \"Hi, I'm Omar\", no \"I am applying\", no \"I would love the "
         "chance\". Write the names exactly: \"Null Labs AI Inc.\" and its product \"Argus\"; \"Waha Bosch Auto Service, a "
         "Bosch Car Service center\".")
# the humanizer skill's tells that VOICE does not already ban, so the email reads like Omar wrote it
HUMAN = ("Sound like a person writing to one reader. Every sentence adds a fact the reader does not have. Vary sentence "
         "length. Use is, are and has, not \"serves as\" or \"stands as\". Never write: \"Let's\", \"Here's the thing\", "
         "\"Honestly\", \"the real question\", \"at its core\", \"I hope this finds you well\", \"I hope this helps\", \"let "
         "me know\", crucial, pivotal, key (as an adjective), valuable, showcase, highlight, underscore, enhance, "
         "meticulous, landscape, testament, quietly. No -ing phrase tacked on to sound deeper (\"ensuring\", \"reflecting\"), "
         "no stacked hedges, no em or en dashes, straight quotes only.")
SIGNATURE = "omarlabs.dev · github.com/omarshalabyy1"  # under the name, written by the code so it is always right
# the startup's own context before any draft: Anthropic's server-side web tools (no loop of ours to run)
WEB = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 5},
       {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 3}]
RESUMES = 3  # a pause_turn (the server's own tool loop hit its limit) is resumed at most this often
RESEARCH = ("You research one startup for a cold email from a data and AI engineer. Search the web and read the company's own "
            "site first, then press and startup databases; never LinkedIn. Write a brief of at most 200 words in plain "
            "markdown: what it sells and to whom; its stage, size and funding; recent launches or news with dates; what its "
            "product and this open role say about the data, AI or reporting problems it has now. Give the URL after each "
            "fact. Say what you could not find; never fill a gap with a guess.")

JOBS = ("SELECT s.job_id, s.title, s.company, j.description, s.score FROM mart.job_status s JOIN core.job j USING (job_id)"
        " WHERE s.score >= %s AND s.status IN ('new', 'saved') AND s.first_seen >= current_date - %s"
        " ORDER BY s.score DESC, s.first_seen DESC")


def startups() -> dict[str, dict]:
    """The startups by name_key, each its row of output/startups/startups.csv; none without the file."""
    if not STARTUPS.exists():
        return {}
    return {name_key(r["name"]): r for r in pd.read_csv(STARTUPS, dtype=str).fillna("").to_dict("records") if r["name"]}


def contacts() -> dict[str, dict]:
    """The person to write to at each company (name_key): a Founder before a Hiring row, and only one with an email."""
    if not CONTACTS.exists():
        return {}
    rows = pd.read_csv(CONTACTS, dtype=str).fillna("")
    rows = rows[rows["side"].isin(SIDES) & rows["email"].str.contains("@")]
    people: dict[str, dict] = {}
    for _, r in rows.assign(order=rows["side"].map(SIDES.index)).sort_values("order", kind="stable").iterrows():
        people.setdefault(name_key(r["company"]), r)
    return people


def startup_jobs(conn) -> list[tuple]:
    """The window's startup roles scored MIN_SCORE or more that you have not acted on: (job_id, title, company,
    description, score)."""
    names = startups()
    return [j for j in conn.execute(JOBS, (MIN_SCORE, HOURS_OLD // 24)).fetchall() if name_key(j[2]) in names]


def without_contact(conn) -> list[str]:
    """The startups with a role for you and no one to write to: what the founders research looks for."""
    people = contacts()
    return sorted({company for _, _, company, _, _ in startup_jobs(conn) if name_key(company) not in people})


def brief(client, company: str, startup: dict, title: str, description: str | None) -> str | None:
    """What Claude found on the web about the startup, kept in output/outreach/briefs/<company>.md and read from there
    on later runs; None when the call failed or never finished (the draft waits for the next run)."""
    import anthropic  # for its errors; only a run that calls Claude pays for the SDK

    path = OUTPUT / "outreach" / "briefs" / f"{slug(company, '') or 'company'}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    known = ", ".join(f"{k}: {startup[k]}" for k in ("what", "kind", "city", "size", "website", "careers") if startup.get(k))
    messages = [{"role": "user", "content": f"The startup: {company} ({known or 'nothing more known'}).\n"
                                            f"The open role: {title}\n{(description or 'No description.')[:2000]}"}]
    try:
        for _ in range(RESUMES + 1):
            r = client.messages.create(model=SETTINGS["claude"]["model"], max_tokens=4000, system=RESEARCH, tools=WEB,
                                       output_config={"effort": "low"}, messages=messages)
            if r.stop_reason != "pause_turn":
                break
            messages.append({"role": "assistant", "content": r.content})  # sent back as is: the server resumes its search
        else:
            print(f"WARNING outreach_drafts: the research on {company} did not finish, tried again next run")
            return None
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
        print(f"WARNING outreach_drafts: the research on {company}: {e!r}"[:300] + ", tried again next run")
        return None
    # the brief is the text after the last search or fetch: text between tool calls is Claude's own working notes
    last_tool = max((i for i, b in enumerate(r.content) if b.type != "text"), default=-1)
    text = "\n".join(b.text for b in r.content[last_tool + 1:] if b.type == "text" and b.text).strip()
    if not text:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    return text


def outreach_drafts(conn) -> None:
    client = draft_client("outreach_drafts")
    if client is None:
        print("outreach_drafts: off in settings.yaml (features.outreach_drafts), or no ANTHROPIC_API_KEY")
        return
    people, known, written, missing = contacts(), startups(), 0, set()
    for job_id, title, company, description, score in startup_jobs(conn):
        person = people.get(name_key(company))
        if person is None:
            missing.add(company)
            continue
        path = OUTPUT / "outreach" / f"{slug(company, title) or 'outreach'}.md"
        if path.exists():
            continue
        cv = cv_for(conn, job_id)
        if cv is None:
            print("outreach_drafts: no CV uploaded in the tracker, skipped")
            return
        context = brief(client, company, known.get(name_key(company), {}), title, description)
        if context is None:
            continue
        system = [{"type": "text", "text": f"You write one short cold email from Omar Shalaby to a person at a startup about an "
                   f"open role there. His CV is in <cv>, the startup in <company> (researched on the web), the role in <job>, "
                   f"the person in <to>. {SAFETY} Never claim a fact about the startup that is not in <company> or <job>.\n\n"
                   f"{BRAND}\n\nSell to what they need: from <company> and <job> work out what they sell, to whom, and the "
                   f"problem behind this hire, then say in their terms (their product, their customers, their data) what Omar "
                   f"would do for them in his first months, backed by what he built before.\n\n"
                   f"Subject: a plain line naming their problem. Body: {WORDS - 20} words at most, to that person by first "
                   f"name: their problem, what Omar would do for them in their first months and the proof from <cv> with its "
                   f"result, then one clear ask for a short call. No attachments mentioned. "
                   f"End with the name Omar Shalaby only.\n\nVoice: {VOICE}\n\n{HUMAN}"},
                  {"type": "text", "cache_control": {"type": "ephemeral"}, "text": f"<cv>\n{cv}\n</cv>"}]
        user = (f"<to>\n{person['name']}, {person['title']} at {company}\n</to>\n<company>\n{context}\n</company>\n"
                f"<job>\n{title} at {company}\n{(description or 'No description.')[:3000]}\n</job>")
        draft, out_of_credit = ask_for_draft(client, system, user, FollowUp, "low", 700, f"outreach {company}")
        if out_of_credit:
            break
        if draft is None:
            continue
        if (not (draft.subject.strip() and draft.body.strip()) or len(draft.body.split()) > WORDS
                or GAP_WORDS.search(f"{draft.subject}\n{draft.body}") or re.search("[—–]", f"{draft.subject}\n{draft.body}")):
            print(f"WARNING outreach_drafts: the draft for {company} was empty, over {WORDS} words, named a gap or used a dash: "
                  "thrown away, tried again next run")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8") as f:  # 'x': two runs never both write it
                f.write(f"To: {person['name']} <{person['email']}>\nSubject: {draft.subject.strip()}\n\n{draft.body.strip()}\n"
                        f"{SIGNATURE}\n\n"
                        f"---\nDraft to {person['name']}, {person['title']} at {company}, for {title} (score {score}). "
                        f"Why them: {person['why']}. Research: output/outreach/briefs/{slug(company, '')}.md. Not sent.\n")
        except FileExistsError:
            continue
        written += 1
    print(f"outreach_drafts: {written} written to {OUTPUT / 'outreach'}"
          + (f"; no founder email for {', '.join(sorted(missing))}: python -m job_radar.research founders" if missing else ""))
