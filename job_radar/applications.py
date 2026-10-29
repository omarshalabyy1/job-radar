"""track_applications: your applications as your mailboxes tell them, read-only, into core.application_email.

Gmail's All Mail is searched over IMAP, in each Gmail account of MAILBOXES in .env (else GMAIL_USER), for application
words in the subject, and for every email from the addresses or domains of APPLICATION_SENDERS in .env (nothing is marked read, moved or deleted, and nothing leaves the laptop but the few emails no
pattern fits, which go to Claude as 2,000 characters each). A mailbox with no email stored yet is read 400 days back,
one with some 3 days; an email is stored once, whichever mailbox it came from (its Message-ID). Each email is told
apart by plain patterns on its sender and subject first (PATTERNS: free); only the leftovers go to
Claude (settings.yaml claude.model), a leftover with no ANTHROPIC_API_KEY or no credit is stored
nowhere, so a later run classifies it. A stored email is never read again.

A matching job in core.job (the same title at a company of the same name when the email names a title, else the only job of that company;
never a job first seen after the email) gets the email's job_id,
and its status in core.application moves up only (applied, interview, rejected, offer), never down.
Switched by features.track_applications in settings.yaml: off, it does not touch the mailbox.

Two more switches, both off when missing: features.follow_up_drafts drafts a short follow-up email for an
application whose first confirmation email is exactly 7 or 14 Cairo calendar days old and has no answer yet
(output/followups/<day>/<company>-<title>.md), and features.interview_prep writes notes for a new interview
email (output/interviews/<company>-<title>/notes.md), once per company and title: the email's own text (fetched
read-only, BODY.PEEK) and the matching job's description, never anything else, and an interview email whose notes
are not written yet is tried again on the next run (notes the checks threw away: tomorrow). Claude writes both in
your voice from your CV (tailor's rules);
they are drafts in files, nothing is sent. funnel_counts and
application_stages feed the tracker's funnel."""

from __future__ import annotations

import email
import email.policy
import imaplib
import os
import re
from email.utils import parseaddr, parsedate_to_datetime
from datetime import date, datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from pydantic import BaseModel

from . import claude
from .config import OUTPUT, SETTINGS
from .sources.inbox import MAIL_BYTES
from .tailor import GAP_WORDS, RULES, slug  # GAP_WORDS: Claude's text with one is thrown away, not written

# an offer only as a job offer's words ("Offer Letter", "Job Offer", "Offer of Employment"): "offer" alone brought food deals
SEARCH = ("(subject:(applying OR application OR applied OR interview OR candidate OR position OR interest OR steps)"
          " OR subject:(offer letter) OR subject:(job offer) OR subject:(employment offer))")
BACKFILL_DAYS, DAILY_DAYS, GAP_DAYS = 400, 3, 14
BODY_CHARS = 2000  # of an email sent to Claude
# the statuses in the order an application moves up; 'viewed' and 'other' move nothing, 'ignored' is yours
LADDER = ["new", "saved", "applied", "interview", "rejected", "offer"]
FOLLOW_UP_DAYS = (7, 14)  # days after the first confirmation email on which a follow-up is drafted
FOLLOW_UP_WORDS = 120  # the most words in a follow-up's body
ANSWERS = ("rejected", "interview", "offer", "hired")  # news that an application was answered
FUNNEL = ["applied", "viewed", "interview", "offer", "hired"]  # the stages, in order
# the voice lines of tailor's RULES (after "Voice, in all three:"), so these drafts sound like the CV drafts
VOICE = next(p for p in RULES.split("\n\n") if p.startswith("Voice")).split(":", 1)[1].strip()
SAFETY = ("Never invent an employer, job title, date, degree, certification, skill, client or number: every one must be "
          "in <cv> or <job>. Never mention a gap or something the candidate has not done.")
CAIRO = ZoneInfo("Africa/Cairo")  # the day an application is 7 or 14 days old, for the email and the drafts

# Tried in order on "sender || subject"; the first that fits gives the kind, and the named groups
# company and title are what it found. SELF stands for your own address (an application you mailed).
# An email no pattern fits is a leftover for Claude.
PATTERNS = [
    (r"^[^|]*(jobalerts-noreply|newsletters-noreply|github\.com|redditmail|notion\.so|dribbble|wellfound)", "other"),
    (r"linkedin.*\|\| Your application was viewed by (?P<company>.+)", "viewed"),
    (r"linkedin.*\|\| [^,]+, your application was sent to (?P<company>.+)", "applied"),
    (r"linkedin.*\|\| Your application to (?P<title>.+) at (?P<company>.+)", "rejected"),  # LinkedIn's "your update" mail
    (r"^SELF \|\| Application (?:for|[–-]) (?P<title>.+?)(?: [–-] .+)?$", "applied"),
    (r"hirevue.*\|\| Interview with (?P<company>.+)", "interview"),
    (r"\|\| (?:AI )?Interview Invite for (?P<title>.+?)(?: - .*)?$", "interview"),
    (r"started your application with", "other"),  # begun, not sent: "Thank you for your application" follows
    (r"smartrecruiters.*\|\| (?P<company>.+?) - Thank you for your interest", "rejected"),
    (r"(?P<company>[^\"]+?) @ icims.*\|\| Thank you for (?:applying|your application)", "applied"),
    (r"\|\| (?P<company>.+?) - Thank you for your application", "applied"),
    (r"\|\| Your application for the position (?P<title>.+) at (?P<company>.+)", "applied"),
    (r"\|\| Confirming your (?P<company>.+?) job application", "applied"),
    (r"\|\| (?P<company>.+?)(?: Group)? [–-] New Job Application Received", "applied"),
    (r"\|\| Thank(?:s| you) for applying (?:to|at) (?P<company>.+)", "applied"),
    (r"\|\| Thank you for your application (?:to|at) (?P<company>.+)", "applied"),
]


class FollowUp(BaseModel):
    subject: str
    body: str


class Question(BaseModel):
    question: str
    answer: str


class Story(BaseModel):
    title: str
    story: str


class Prep(BaseModel):
    role_needs: list[str]
    questions: list[Question]
    stories: list[Story]
    ask_them: list[str]


class Mail(BaseModel):
    kind: Literal["applied", "viewed", "rejected", "interview", "offer", "hired", "other"]
    company: str  # "" when the email does not say
    title: str


def clean(text: str) -> str:
    """One line without zero-width characters (SmartRecruiters puts them round the company) or inline HTML tags (Raya's
    subject came as 'with <b><span>AMAN Holding</span></b>'); a sender's <address> is kept."""
    text = re.sub(r"</?(?:b|i|u|em|strong|span|font)\b[^>]*>", "", text, flags=re.I)
    return " ".join(re.sub(r"[​-‏⁠﻿\x00]", "", text).split())


def classify(sender: str, subject: str, me: str = "") -> tuple[str, str | None, str | None] | None:
    """(kind, company, title) from the first pattern that fits the sender and subject, or None when
    none does. `me` is your own address."""
    sender, subject = clean(sender), clean(subject)
    text = f"{'SELF' if me and me.lower() in sender.lower() else sender} || {subject}"
    for pattern, kind in PATTERNS:
        if m := re.search(pattern, text, re.I):
            found = m.groupdict()
            return kind, *((found.get(g) or "").strip(" .!") or None for g in ("company", "title"))
    return None


def company_names(company: str) -> list[str]:
    """The names a company goes by, as job_key writes a company: its name without a bracketed note, and each bracketed
    note ('Boston Consulting Group (BCG)': both)."""
    from .steps import job_key  # steps imports this module

    names = [re.sub(r"\(.*?\)", " ", company), *re.findall(r"\((.*?)\)", company)]
    return [job_key("", n, "-").split(" | ", 1)[1] for n in names]


def same_company(a: str, b: str) -> bool:
    """One of a's names starts with, or is, one of b's ('BCG' and 'BCG X'; 'SourceQS (recruiter)' and 'SourceQS')."""
    return any(x.startswith(y) or y.startswith(x) for x in company_names(a) for y in company_names(b) if x and y)


def find_job(jobs: list[tuple[int, str, str, date]], title: str | None, company: str | None, received: date) -> int | None:
    """The job_id of (job_id, title, company, first_seen) in `jobs` that an email of `received` is
    about: the same title (as job_key reads it) at a company that goes by the same name when the email names a
    title, else the only job of that company. A job first seen after the email is a later posting, not the one the
    email is about."""
    from .steps import job_key  # steps imports this module

    if not company:
        return None
    jobs = [j for j in jobs if j[3] <= received]
    if title:
        key = job_key(title, "", "-").split(" | ", 1)[0]
        return next((job_id for job_id, t, c, _ in jobs if job_key(t, "", "-").split(" | ", 1)[0] == key and same_company(c, company)),
                    None)
    ids = [job_id for job_id, _, c, _ in jobs if same_company(c, company)]
    return ids[0] if len(ids) == 1 else None


def body_text(msg) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    text = part.get_content()
    if part.get_content_type() == "text/html":
        soup = BeautifulSoup(text, "html.parser")
        for tag in soup(["style", "script", "head"]):
            tag.decompose()
        text = soup.get_text(" ")
    return " ".join(text.split())[:BODY_CHARS]


def accounts() -> dict[str, str]:
    """{address: app password} of the Gmail accounts to read: those of MAILBOXES in .env (address:app-password[:imap-host],
    comma-separated; this search is Gmail's own, so another provider's entry is left to extract_email), else GMAIL_USER."""
    found, others = {}, 0
    for entry in os.environ.get("MAILBOXES", "").split(","):
        address, password, *_ = [*entry.strip().split(":"), ""]
        if address.lower().endswith(("@gmail.com", "@googlemail.com")) and password.strip():
            found[address.lower()] = password.replace(" ", "")
        elif entry.strip():
            others += 1
    if others:
        print(f"track_applications: {others} MAILBOXES entries are not Gmail accounts with a password, left out")
    user, password = os.environ.get("GMAIL_USER", "").lower(), os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    if not found and user and password:
        found[user] = password
    return found


def senders() -> dict[str, str | None]:
    """APPLICATION_SENDERS in .env, comma-separated: {address or domain: its company}. An entry is an address or a domain,
    with "=Company" to name it; a domain alone names its own ("acme-labs.io": Acme Labs), an address alone none."""
    found = {}
    for entry in os.environ.get("APPLICATION_SENDERS", "").split(","):
        who, _, company = entry.partition("=")
        if who := who.strip().lower():
            found[who] = company.strip() or (None if "@" in who else re.sub(r"[-_.]", " ", who.rsplit(".", 1)[0]).title())
    return found


def search() -> str:
    """SEARCH, and with APPLICATION_SENDERS every email from them too: people who hire you without an application
    word in the subject (an intro call, a welcome)."""
    listed = senders()
    return f"({SEARCH} OR from:({' OR '.join(listed)}))" if listed else SEARCH


def sender_company(sender: str) -> str | None:
    """The company APPLICATION_SENDERS gives the sender's address, or its domain (a subdomain too), or None."""
    address = parseaddr(sender)[1].lower()
    domain = address.rpartition("@")[2]
    return next((company for who, company in senders().items()
                 if who == address or domain == who or domain.endswith(f".{who}")), None)


def read_mailbox(user: str, password: str, days: int, stored: set[str]) -> tuple[list[dict], list[dict], int]:
    """(classified, leftovers, found): the emails of the last `days` days not stored yet, those a
    pattern told apart and those no pattern fits (with their text), and how many the search found."""
    classified, leftovers = [], []
    with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
        imap.login(user, password)
        if imap.select('"[Gmail]/All Mail"', readonly=True)[0] != "OK":
            raise RuntimeError("track_applications: Gmail's [Gmail]/All Mail could not be opened")
        # X-GM-RAW takes Gmail's own search; no inner quotes (imaplib cannot parse them)
        uids = imap.uid("search", None, "X-GM-RAW", f'"{search()} newer_than:{days}d"')[1][0].split()
        for uid in uids:
            info, head = imap.uid("fetch", uid, "(RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT MESSAGE-ID DATE)])")[1][0]
            h = email.message_from_bytes(head, policy=email.policy.default)
            message_id = str(h["Message-ID"] or f"{user}/All Mail/{uid.decode()}").strip()
            size = int(re.search(rb"RFC822\.SIZE (\d+)", info).group(1))
            if message_id in stored or str(h["Subject"]).startswith("Job radar") or size > MAIL_BYTES:
                continue  # stored before, its own digest, or attachments
            sender, subject = clean(str(h["From"])), clean(str(h["Subject"]))
            try:
                received_at = parsedate_to_datetime(str(h["Date"]))
            except (TypeError, ValueError):
                print(f"WARNING track_applications: no date on {subject[:60]!r}, skipped")
                continue
            mail = {"message_id": message_id, "received_at": received_at.astimezone(timezone.utc) if received_at.tzinfo
                    else received_at.replace(tzinfo=timezone.utc), "sender": sender, "subject": subject,
                    "to": clean(str(h["To"] or ""))}
            found = classify(sender, subject, user)
            if found:  # the kind, company and title from the patterns
                classified.append({**mail, "kind": found[0], "company": found[1], "title": found[2]})
            else:  # only now is the email itself downloaded
                msg = email.message_from_bytes(imap.uid("fetch", uid, "(BODY.PEEK[])")[1][0][1], policy=email.policy.default)
                leftovers.append({**mail, "text": body_text(msg)})
    return classified, leftovers, len(uids)


def fetch_body(user: str, password: str, message_id: str) -> str | None:
    """The text of the email with this Message-ID (BODY_CHARS characters), downloaded read-only (BODY.PEEK),
    or None when Gmail's All Mail has no such email."""
    with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
        imap.login(user, password)
        if imap.select('"[Gmail]/All Mail"', readonly=True)[0] != "OK":
            raise RuntimeError("track_applications: Gmail's [Gmail]/All Mail could not be opened")
        uids = imap.uid("search", None, "HEADER", "Message-ID", f'"{message_id}"')[1][0].split()
        if not uids:
            return None
        return body_text(email.message_from_bytes(imap.uid("fetch", uids[0], "(BODY.PEEK[])")[1][0][1], policy=email.policy.default))


# What each kind means, for Claude. The mistakes it made with a one-line definition are named (2026-10-11): the
# candidate's own replies read as offers, "track your application" as viewed, a thank-you as rejected, a job board as
# the company.
CLASSIFY = """You read one email from a job seeker's mailbox and say what it tells about one of their job applications.

kind, judged from the whole email, not its subject alone:
- applied: the employer confirms it received the application ("we received", "thank you for applying", "track your
  application"), or the candidate themself sends an application, a CV or an expression of interest to an employer.
- viewed: the employer says it opened or is reviewing the application, with no decision yet.
- interview: the employer invites them to, schedules or confirms an interview, a call with a recruiter or hiring
  manager, an assessment or a test, or writes to arrange one (a recruiter's introduction leading to a call counts).
- offer: the employer offers the job: an offer letter or a contract sent to sign, or a reminder to sign it.
- hired: the offer is accepted or signed (a "you signed" receipt, the candidate's acceptance), or the employer welcomes
  them aboard: "welcome to the team", onboarding, accounts and credentials, a start date.
- rejected: the employer says it will not move forward with them. A thank-you with no decision is applied, not rejected.
- other: everything else - newsletters, articles and posts about interviews or offers, job alerts and "jobs similar to
  your application", marketing and deals, "complete" or "finish your application" (not sent yet), account or security
  notices, bounces, and the candidate's own replies, forwards and calendar answers in a thread.

The mailbox belongs to the candidate: an email from that address was written by them.
company: the employer, never a job board, an applicant-tracking or e-signature service or a newsletter (LinkedIn, Bayt,
Wuzzuf, Glassdoor, Workable, Dropbox Sign ...); empty when the email does not name the employer. title: the job, empty
when it does not say."""


def ask_claude(leftovers: list[dict], cfg: dict, me: str = "") -> list[dict]:
    """Claude's kind, company and title for each leftover of the mailbox `me`; one it could not be asked (the credit ran
    out, an error or a timeout) or whose answer did not come back whole is left out, for the next run."""
    import anthropic  # for its errors; the SDK loads only in a run that calls Claude, and the tracker imports this module
    client = claude.client()  # a stuck call gives up inside the DAG's 300 s
    classified = []
    for mail in leftovers:
        try:
            r = client.messages.parse(  # 1,024 tokens: it thinks first, and 200 could cut an answer off
                model=cfg["model"], max_tokens=1024, system=CLASSIFY, output_format=Mail, output_config={"effort": "low"},
                messages=[{"role": "user", "content": f"Mailbox: {me}\nFrom: {mail['sender']}\nTo: {mail['to']}\n"
                                                      f"Subject: {mail['subject']}\n\n{mail['text']}"}])
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:  # a timeout is a connection error
            if claude.out_of_credit(e):  # the balance ran out: every call would be refused
                print("WARNING track_applications: the API credit ran out, the rest wait for the next run")
                break
            print(f"WARNING track_applications: {mail['subject'][:60]!r}: {e!r}"[:300] + ", left for the next run")
            continue
        if r.parsed_output is not None:
            m = r.parsed_output
            classified.append({**mail, "kind": m.kind, "company": clean(m.company).strip(" .!") or None,
                               "title": clean(m.title).strip(" .!") or None})
    return classified


def name_key(text: str | None) -> str:
    """A company by its letters and digits alone, lowercase ("Acme, Ltd." and "ACME Ltd" are one)."""
    return re.sub(r"[\W_]+", "", (text or "").lower())


def cairo_today() -> date:
    return datetime.now(CAIRO).date()


def due_follow_ups(rows: list[tuple], today: date) -> list[tuple[str, str | None, date, int]]:
    """(company, title, first applied day, days) for each company and title whose first 'applied' email is
    exactly 7 or 14 days old on `today` (Cairo calendar days) and that has no later rejected, interview or offer email from
    the company (or, when that email names no company, with its title). `rows` are (company, title, kind,
    received_at) of core.application_email. An application with no company is skipped: nobody to write to."""
    first: dict[tuple[str, str], tuple] = {}
    for company, title, kind, at in sorted(rows, key=lambda r: r[3]):
        if kind == "applied" and company:
            first.setdefault((name_key(company), (title or "").lower()), (at, company, title))
    answers = [(name_key(c), (t or "").lower(), at) for c, t, kind, at in rows if kind in ANSWERS]
    due = []
    for (company_key, title_key), (at, company, title) in first.items():
        day = at.astimezone(CAIRO).date()
        answered = any(later > at and (c == company_key or (not c and title_key and t == title_key)) for c, t, later in answers)
        if (today - day).days in FOLLOW_UP_DAYS and not answered:
            due.append((company, title, day, (today - day).days))
    return sorted(due, key=lambda d: (d[2], d[0].lower(), d[1] or ""))


def due_now(conn, today: date | None = None) -> list[tuple[str, str | None, date, int]]:
    """due_follow_ups of the mailbox (core.application_email) on `today`, a Cairo day (default today): what the email lists
    and the drafts are written for."""
    # an email matched to a job goes by that job's company: Raya's rejection named AMAN Holding but answers Raya's job
    return due_follow_ups(conn.execute("SELECT coalesce(j.company, e.company), e.title, e.kind, e.received_at"
                                       " FROM core.application_email e LEFT JOIN core.job j USING (job_id)"
                                       " WHERE e.kind IN ('applied', 'rejected', 'interview', 'offer', 'hired')").fetchall(),
                          today or cairo_today())


CHANNELS = [("LinkedIn", "linkedin"), ("Workable", "workable"), ("iCIMS", "icims"), ("Workday", "myworkday"),
            ("SmartRecruiters", "smartrecruiters"), ("Recruitera", "recruitera"), ("Wuzzuf", "wuzzuf")]


def channel(sender: str | None, me: str = "") -> str:
    """Where an application went through, from the sender of its first confirmation email: a job board or
    applicant system by name, 'Direct email' when you sent it yourself, else 'Company site'."""
    sender = (sender or "").lower()
    for label, word in CHANNELS:
        if word in sender:
            return label
    return "Direct email" if me and me.lower() in sender else "Company site"


def application_stages(rows: list[tuple], me: str = "") -> list[dict]:
    """One dict per application: its month and channel and the stages reached. An application is a company (an email
    naming none: its title), as the tracker's Applications table has it, with an 'applied', 'interview', 'offer' or
    'hired' email: a job that came to you (an introduction, a recruiter) has no confirmation, and an interview or an offer
    still counts (a view alone does not). Its month and channel are its first 'applied' email's, else, with none,
    its first email's month and "Direct contact". A stage is reached by its own email or any later stage's (an
    interview means it was viewed), so the counts only fall down the funnel. `rows`: (company, title, kind,
    received_at, sender)."""
    groups: dict[str, dict] = {}
    for company, title, kind, at, sender in sorted(rows, key=lambda r: r[3]):
        company, title = (x if isinstance(x, str) and x else None for x in (company, title))
        key = name_key(company) or (title or "").lower()
        if not key or kind not in FUNNEL:
            continue
        g = groups.setdefault(key, {"month": None, "channel": None, "first": f"{at:%Y-%m}", "kinds": set()})
        g["kinds"].add(kind)
        if kind == "applied" and g["month"] is None:
            g["month"], g["channel"] = f"{at:%Y-%m}", channel(sender, me)
    return [{"month": g["month"] or g["first"], "channel": g["channel"] or "Direct contact",
             **{stage: bool(g["kinds"] & set(FUNNEL[i:])) for i, stage in enumerate(FUNNEL)}}
            for g in groups.values() if g["kinds"] & {"applied", "interview", "offer", "hired"}]


def funnel_counts(applications: list[dict]) -> dict[str, int]:
    """How many of `applications` (application_stages) reached each stage, applied first."""
    return {stage: sum(a[stage] for a in applications) for stage in FUNNEL}


def ask_for_draft(client, system: list, user: str, fmt, effort: str, max_tokens: int, what: str) -> tuple:
    """(parsed output or None, out of credit): one call. A failure or a timeout gives None and is
    printed, so the draft is tried again on the next run; an empty balance stops the rest."""
    import anthropic  # for its errors; only a run that calls Claude pays for the SDK
    try:
        r = client.messages.parse(model=SETTINGS["claude"]["model"], max_tokens=max_tokens, system=system,
                                  output_format=fmt, output_config={"effort": effort},
                                  messages=[{"role": "user", "content": user}])
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:  # a timeout is a connection error
        if claude.out_of_credit(e):
            print("WARNING track_applications: the API credit ran out, no more drafts until you buy more")
            return None, True
        print(f"WARNING track_applications: {what}: {e!r}"[:300] + ", left for the next run")
        return None, False
    return r.parsed_output, False


def cv_for(conn, job_id: int | None) -> str | None:
    """The CV Claude graded best for the job, else your newest CV, else None."""
    row = conn.execute("SELECT c.text FROM core.claude_grade g JOIN core.cv c ON c.label = g.cv WHERE g.job_id = %s",
                       (job_id,)).fetchone() if job_id else None
    row = row or conn.execute("SELECT text FROM core.cv ORDER BY uploaded_at DESC LIMIT 1").fetchone()
    return row[0] if row else None


def draft_client(switch: str):
    """The Claude client when features.<switch> is on and a key is set, else None (saying why when it is on)."""
    if not (SETTINGS.get("features") or {}).get(switch):
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(f"track_applications: features.{switch} is on but there is no ANTHROPIC_API_KEY in .env, skipped")
        return None
    return claude.client()  # a stuck call gives up inside the DAG's 300 s


def follow_up_drafts(conn, today: date | None = None) -> tuple[int, int]:
    """(written, due): a follow-up draft for each application due on `today` (a Cairo day, default today).
    The file output/followups/<today>/<company>-<title>.md is the record: a draft already there is not asked
    for again, so an hourly run writes each draft once, and the 14th day is a draft of its own."""
    client = draft_client("follow_up_drafts")
    if client is None:
        return 0, 0
    today = today or cairo_today()
    due = due_now(conn, today)
    jobs = conn.execute("SELECT job_id, title, company, first_seen FROM core.job WHERE in_scope").fetchall()
    written = 0
    for company, title, applied_on, days in due:
        path = OUTPUT / "followups" / str(today) / f"{slug(company, title) or 'follow-up'}.md"
        if path.exists():
            continue
        job_id = find_job(jobs, title, company, applied_on)
        cv = cv_for(conn, job_id)
        if cv is None:
            print("track_applications: no CV uploaded in the tracker, follow-ups skipped")
            break
        job = conn.execute("SELECT description FROM core.job WHERE job_id = %s", (job_id,)).fetchone() if job_id else None
        system = [{"type": "text", "text": f"You write one short follow-up email for a candidate who applied for a job and has not "
                   f"heard back. The candidate's CV is in <cv>, the job when known in <job>. {SAFETY} Subject: a plain "
                   f"line naming the role or the company. Body: {FOLLOW_UP_WORDS - 20} words at most, written to the hiring team: "
                   f"one fact from <cv> that matters to their role, then one clear, polite ask for an update on the application "
                   f"or a short call. Sign with the candidate's name only.\n\nVoice: {VOICE}"},
                  {"type": "text", "cache_control": {"type": "ephemeral"}, "text": f"<cv>\n{cv}\n</cv>"}]
        user = (f"<job>\n{title or 'role not named in the confirmation email'} at {company}; applied {applied_on}, "
                f"{days} days ago.\n{(job[0] or '')[:3000] if job else 'No job description.'}\n</job>")
        draft, out_of_credit = ask_for_draft(client, system, user, FollowUp, "low", 700, f"follow-up {company}")
        if out_of_credit:
            break
        if draft is None:
            continue
        if (not (draft.subject.strip() and draft.body.strip()) or len(draft.body.split()) > FOLLOW_UP_WORDS
                or GAP_WORDS.search(f"{draft.subject}\n{draft.body}")):
            print(f"WARNING track_applications: follow-up for {company} was empty, over {FOLLOW_UP_WORDS} words or named a gap: "
                  "thrown away, tried again next run")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8") as f:  # 'x': two runs never both write it
                f.write(f"Subject: {draft.subject.strip()}\n\n{draft.body.strip()}\n\n---\n"
                        f"Draft for {company}{f' ({title})' if title else ''}, applied {applied_on}, {days} days ago. Not sent.\n")
        except FileExistsError:
            continue
        written += 1
    return written, len(due)


def interview_prep(conn, mails: list[dict], today: date | None = None) -> tuple[int, int]:
    """(written, asked about): notes for each interview email of `mails` (message_id, company, title, sender,
    subject, received_at, job_id, mailbox) in output/interviews/<company>-<title>/notes.md, once per company and title. Only
    those with no notes file yet are asked about. Notes thrown away by the checks leave a .tried-<today> file (a Cairo
    day, default today) next to where they would be, so that prep is asked for again tomorrow, not every hour; one
    whose call failed or whose email could not be read is tried again on the next run. Its source is
    the email's own text (downloaded read-only from its mailbox) and the matching job's description; with neither, the notes say so."""
    client = draft_client("interview_prep")
    if client is None:
        return 0, 0
    today = today or cairo_today()
    todo = [(m, OUTPUT / "interviews" / name / "notes.md") for m in mails if (name := slug(m["company"], m["title"]))]
    todo = [(m, path) for m, path in todo if not path.exists() and not (path.parent / f".tried-{today}").exists()]
    boxes = accounts()
    written = 0
    for m, path in todo:
        cv = cv_for(conn, m.get("job_id"))
        if cv is None:
            print("track_applications: no CV uploaded in the tracker, interview prep skipped")
            break
        if m.get("mailbox") not in boxes:  # the email is read again from the mailbox it came from
            print(f"WARNING track_applications: interview prep for {m['company']}: its mailbox is not in MAILBOXES, skipped")
            continue
        try:
            text = fetch_body(m["mailbox"], boxes[m["mailbox"]], m["message_id"])
        except (imaplib.IMAP4.error, OSError) as e:
            print(f"WARNING track_applications: interview prep for {m['company']}: the email could not be read: {e!r}"[:300]
                  + ", left for the next run")
            continue
        if text is None:
            print(f"WARNING track_applications: interview prep for {m['company']}: the email is not in All Mail, skipped")
            continue
        job = conn.execute("SELECT title, company, description FROM core.job WHERE job_id = %s",
                           (m["job_id"],)).fetchone() if m.get("job_id") else None
        applied = [t for (t,) in conn.execute(
            "SELECT DISTINCT title FROM core.application_email WHERE kind = 'applied' AND title IS NOT NULL"
            " AND received_at <= %s AND regexp_replace(lower(company), '[\\W_]+', '', 'g') = %s",
            (m["received_at"], name_key(m["company"])))] if m["company"] else []
        source = (f"The email: from {m['sender']}, subject {m['subject']}.\n{text or '(no text)'}\n"
                  + (f"The job in the radar: {job[0]} at {job[1]}\n{(job[2] or '')[:4000]}" if job else "No job description is stored.")
                  + (f"\nRoles the candidate applied for at this company: {', '.join(applied)}." if applied else ""))
        system = [{"type": "text", "text": f"You prepare a candidate for one job interview. The candidate's CV is in <cv>, what is known "
                   f"about the role in <source>. {SAFETY} Use only <source> and <cv>: invent nothing about the employer, the role, "
                   "the interview, its date or format, or who runs it. Where <source> gives nothing for a point, write exactly "
                   "'No detail given in the email'. role_needs: what the role asks for, 4-6 short points, each only from <source>; "
                   "when <source> says nothing about the role, one point: 'No detail given in the email'. questions: exactly 8 likely "
                   "interview questions, each with an answer in the candidate's voice drawn only from <cv>; when <source> names no "
                   "role, general questions on the CV. stories: exactly 3, the candidate's strongest from <cv>, each the situation, "
                   "the action and the result, with the numbers <cv> gives. ask_them: 4 questions the candidate asks the "
                   f"interviewer, none that assumes a fact <source> does not give. Answers are 2-4 sentences.\n\nVoice: {VOICE}"},
                  {"type": "text", "cache_control": {"type": "ephemeral"}, "text": f"<cv>\n{cv}\n</cv>"}]
        prep, out_of_credit = ask_for_draft(client, system, f"<source>\n{source}\n</source>", Prep, "medium", 4000,
                                            f"interview prep {m['company']}")
        if out_of_credit:
            break
        if prep is None:
            continue
        notes = "\n".join(["## What the role needs", *[f"- {x}" for x in prep.role_needs],
                           "\n## Likely questions", *[f"{i}. **{q.question}**\n   {q.answer}" for i, q in enumerate(prep.questions, 1)],
                           "\n## Your three strongest stories", *[f"{i}. **{x.title}**\n   {x.story}" for i, x in enumerate(prep.stories, 1)],
                           "\n## Questions to ask them", *[f"- {x}" for x in prep.ask_them], ""])
        path.parent.mkdir(parents=True, exist_ok=True)
        if len(prep.questions) != 8 or len(prep.stories) != 3 or not prep.role_needs or GAP_WORDS.search(notes):  # Claude's text, not the email's subject
            (path.parent / f".tried-{today}").touch()
            print(f"WARNING track_applications: interview prep for {m['company']} did not have 8 questions and 3 stories or "
                  "named a gap: thrown away, tried again tomorrow")
            continue
        md = "\n".join([f"# Interview: {m['company'] or m['title']}" + (f" ({m['title']})" if m["company"] and m["title"] else ""),
                        f"\nFrom the email '{m['subject']}', received {m['received_at']:%Y-%m-%d}. Draft notes, from the email, the job "
                        "text and your CV only.\n", notes])
        try:
            with path.open("x", encoding="utf-8") as f:
                f.write(md)
        except FileExistsError:
            continue
        written += 1
    return written, len(todo)


def move_status(cur, job_id: int, status: str, note: str) -> int:
    """Give the job `status` in core.application (with the note added) when that is higher on LADDER than its status now,
    or it has none: up only, never down. 'ignored' is yours and not on the ladder, so it never moves. 1 when it changed."""
    cur.execute("INSERT INTO core.application (job_id, status, note) VALUES (%s, %s, %s)"
                " ON CONFLICT (job_id) DO UPDATE SET status = EXCLUDED.status,"
                " note = concat_ws('; ', core.application.note, EXCLUDED.note), updated_at = now()"
                " WHERE array_position(%s::text[], core.application.status) < array_position(%s::text[], EXCLUDED.status)",
                (job_id, status, note, LADDER, LADDER))
    return cur.rowcount


def store(cur, jobs: list[tuple], mailbox: str, mails: list[dict]) -> tuple[int, int, int, dict[str, int]]:
    """Add `mails` (oldest first, so the status climbs in order) to core.application_email with the mailbox they were read
    from, and move the status of the job each is about. An email already there (its Message-ID) is skipped. Returns (new,
    matched to a job, statuses moved up, new by kind)."""
    added, matched, moved, kinds = 0, 0, 0, {}
    for m in mails:
        m = {**m, "company": m["company"] or sender_company(m["sender"])}  # the email named none: its sender's, when listed
        job_id = find_job(jobs, m["title"], m["company"], m["received_at"].date())
        cur.execute("INSERT INTO core.application_email (message_id, received_at, sender, subject, company, title, kind, job_id, mailbox)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (message_id) DO NOTHING",
                    (m["message_id"], m["received_at"], m["sender"], m["subject"], m["company"], m["title"], m["kind"], job_id, mailbox))
        if cur.rowcount == 0:
            continue
        added += 1
        kinds[m["kind"]] = kinds.get(m["kind"], 0) + 1
        if job_id:
            matched += 1
        status = "offer" if m["kind"] == "hired" else m["kind"]  # a job's status stops at offer: hired is one
        if job_id and status in LADDER:
            moved += move_status(cur, job_id, status, f"from your mailbox {m['received_at']:%Y-%m-%d}")
    return added, matched, moved, kinds


# applicant tracking systems that send every company's emails from one address: their sender names no single job
SHARED_SENDERS = ("greenhouse", "lever.co", "myworkday", "smartrecruiters", "ashbyhq", "workable", "linkedin", "indeed",
                  "wuzzuf", "bayt", "recruitee", "teamtailor", "bamboohr", "jobvite", "icims", "breezy")


def same_sender_job(cur, sender: str, received_at) -> int | None:
    """The one job the earlier emails from this exact address are about (Raya's rejection named its holding company,
    AMAN Holding, not Raya), or None: an address linked to several jobs, or an applicant tracking system's."""
    address = parseaddr(sender)[1].lower()
    if not address or any(s in address.rpartition("@")[2] for s in SHARED_SENDERS):
        return None
    ids = [job_id for (job_id,) in cur.execute(
        "SELECT DISTINCT job_id FROM core.application_email WHERE job_id IS NOT NULL AND received_at <= %s"
        " AND lower(sender) LIKE %s", (received_at, f"%{address}%")).fetchall()]
    return ids[0] if len(ids) == 1 else None


def rematch(cur, jobs: list[tuple]) -> tuple[int, int]:
    """(matched, moved up): the stored emails with no job, matched again (the matcher or the jobs may have changed
    since they were read), and their job's status moved as store() does. Oldest first, so the status climbs in order."""
    matched = moved = 0
    for message_id, company, title, kind, received_at, sender in cur.execute(
            "SELECT message_id, company, title, kind, received_at, sender FROM core.application_email"
            " WHERE job_id IS NULL AND kind <> 'other' ORDER BY received_at").fetchall():
        if not (job_id := find_job(jobs, title, company, received_at.date()) or same_sender_job(cur, sender, received_at)):
            continue
        cur.execute("UPDATE core.application_email SET job_id = %s WHERE message_id = %s", (job_id, message_id))
        matched += 1
        status = "offer" if kind == "hired" else kind
        if status in LADDER:
            moved += move_status(cur, job_id, status, f"from your mailbox {received_at:%Y-%m-%d}")
    return matched, moved


def track_applications(conn) -> None:
    if not (SETTINGS.get("features") or {}).get("track_applications"):
        print("track_applications: off in settings.yaml (features.track_applications)")
        return
    boxes = accounts()
    if not boxes:
        raise RuntimeError("track_applications: no Gmail account: MAILBOXES (address:app-password, comma-separated) or GMAIL_USER "
                           "and GMAIL_APP_PASSWORD are not set in .env")
    stored = {message_id for (message_id,) in conn.execute("SELECT message_id FROM core.application_email")}
    known = dict(conn.execute("SELECT mailbox, max(received_at) FROM core.application_email GROUP BY mailbox").fetchall())
    cfg = SETTINGS.get("claude") or {}
    jobs = conn.execute("SELECT job_id, title, company, first_seen FROM core.job WHERE in_scope").fetchall()
    emails, leftover, asked, added, matched, moved, kinds, failed = 0, 0, 0, 0, 0, 0, {}, 0
    for n, (address, password) in enumerate(boxes.items(), 1):
        # a mailbox with nothing stored yet is read from the start; else from its newest stored email (a gap of a few
        # days is caught up), at least DAILY_DAYS and at most GAP_DAYS
        days = (min(GAP_DAYS, max(DAILY_DAYS, (datetime.now(timezone.utc) - known[address]).days + 2))
                if known.get(address) else BACKFILL_DAYS)
        try:
            classified, leftovers, found = read_mailbox(address, password, days, stored)
            if not found and not known.get(address):
                raise RuntimeError(f"no email in the last {days} days: wrong mailbox or folder?")
        except Exception as e:  # one mailbox down is a short day: the others are still read (the address is never printed)
            print(f"WARNING track_applications: mailbox {n} of {len(boxes)}: {e!r}"[:300])
            failed += 1
            continue
        by_claude = []
        if leftovers and not os.environ.get("ANTHROPIC_API_KEY"):
            print(f"track_applications: mailbox {n}: {len(leftovers)} emails left for Claude: no ANTHROPIC_API_KEY in .env")
        elif leftovers:
            # features.track_applications is this step's switch (checked above); this reads a few emails a day
            by_claude = ask_claude(leftovers, cfg, address)
        mails = sorted(classified + by_claude, key=lambda m: m["received_at"])
        with conn.cursor() as cur:
            new, linked, up, new_kinds = store(cur, jobs, address, mails)
        conn.commit()  # a mailbox at a time: a run killed part way keeps the mailboxes it finished
        stored |= {m["message_id"] for m in mails}  # an email in two mailboxes is stored once, from the first
        print(f"track_applications: mailbox {n} of {len(boxes)}: {found} emails found in the last {days} days, {new} new "
              f"({', '.join(f'{k} {c}' for k, c in sorted(new_kinds.items())) or 'none'}), {linked} matched to a job, "
              f"{up} statuses moved up; {len(leftovers)} sent to Claude, {len(by_claude)} classified")
        emails, leftover, asked, added, matched, moved = (emails + found, leftover + len(leftovers), asked + len(by_claude),
                                                         added + new, matched + linked, moved + up)
        for k, c in new_kinds.items():
            kinds[k] = kinds.get(k, 0) + c
    if failed == len(boxes):
        raise RuntimeError("track_applications: no mailbox could be read")
    with conn.cursor() as cur:
        again, up = rematch(cur, jobs)
    conn.commit()
    matched, moved = matched + again, moved + up
    # after the commit, so a failing draft never undoes the mailbox read; each is off unless its switch is on. No prep for
    # a company that already sent an offer (or hired you): its interviews are behind you.
    interviews = [dict(zip(("message_id", "company", "title", "sender", "subject", "received_at", "job_id", "mailbox"), row))
                  for row in conn.execute(
                      "SELECT message_id, company, title, sender, subject, received_at, job_id, mailbox FROM core.application_email e"
                      " WHERE kind = 'interview' AND NOT EXISTS (SELECT 1 FROM core.application_email o WHERE o.kind IN ('offer', 'hired')"
                      " AND regexp_replace(lower(o.company), '[\\W_]+', '', 'g') = regexp_replace(lower(e.company), '[\\W_]+', '', 'g'))"
                      " ORDER BY received_at").fetchall()]
    prep_written, prep_asked = interview_prep(conn, interviews)
    follow_written, follow_due = follow_up_drafts(conn)
    print(f"track_applications: {len(boxes) - failed} of {len(boxes)} mailboxes read, {emails} emails found, {added} new "
          f"({', '.join(f'{k} {n}' for k, n in sorted(kinds.items())) or 'none'}), {matched} matched to a job, "
          f"{moved} statuses moved up; Claude classified {asked} of {leftover} leftovers; drafts: "
          f"{follow_written} of {follow_due} follow-ups, {prep_written} of {prep_asked} interview preps")
