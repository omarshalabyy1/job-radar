"""outreach_send: the cold emails outreach_drafts wrote (output/outreach/<company>-<title>.md), sent from Omar's own Gmail accounts under
the "Outreach sender" guardrails of docs/superpowers/plans/2026-11-08-auto-apply.md. Dry run first: nothing leaves the laptop unless
settings.yaml has outreach.mode: live AND outreach.enabled: true; a file output/outreach.stop stops it too, within one tick.

    eligible(conn, now, cfg)           read-only: the emails the rules allow from `now` on, in order, each with the time it may go
    run(conn, now, cfg)                one tick: writes (dry run) or sends (live) the email that is due, at most one
    suppress_from_replies(conn, rows)  replies, "no", "unsubscribe", bounces and spam complaints -> core.suppression, and the mailbox pause

Run it about every five minutes (the gaps between emails are 2 to 10 minutes): one email per tick keeps the gaps real. The rules, each
with a test in tests/test_outreach_send.py:
- A recipient comes only from a data/contacts.csv row (a Founder or Hiring contact with an email) whose source is the company's own site
  or the press: a URL in its `source` column, or at the end of `why` as `research founders` writes it. Never LinkedIn, an email-finder or
  lead database, or an address the row says was guessed. The draft's To address must be that row's.
- One person is mailed once: never a second cold email (two drafts for one company), and the first mailbox that wrote to them writes
  every follow-up, in the same thread (Message-ID, In-Reply-To, References, "Re: " subject).
- Caps: per mailbox a day, in total a day (Cairo day), per hour, and a random gap of 2 to 10 minutes; only inside working hours in the
  recipient's time zone (a recipient whose place maps to no zone is not mailed). A paused mailbox sends nothing and its share of the
  day's total is removed, never given to the other mailboxes.
- Follow-ups only on day 7 and day 14 after the first email (a late one is skipped for good), at most two.
- core.suppression is forever: a reply, "no", "unsubscribe", a bounce, a spam complaint, or an out-of-office naming a new contact.
- A mailbox whose bounce rate passes outreach.bounce_limit, or that gets a spam complaint, is paused 7 days and ntfy says so.
- Every email is plain text, ends with one opt-out line, has no attachment and no link. A draft that names a gap, fails the humanizer
  check (the bans of outreach.py's prompt), mentions an attachment, has a link or fakes a "Re:" is dropped, never rewritten.
Nothing here prints an address or a password: the log has counts only."""

from __future__ import annotations

import csv
import hashlib
import random
import re
import smtplib
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime, formataddr, make_msgid, parseaddr
from pathlib import Path
from zoneinfo import ZoneInfo

from . import outreach as drafts
from .applications import CAIRO, accounts
from .config import OUTPUT, SETTINGS
from .tailor import GAP_WORDS, slug

DRAFTS = OUTPUT / "outreach"
STOP_FILE = OUTPUT / "outreach.stop"
SENDER_NAME = "Omar Shalaby"
OPT_OUT = 'If this is not for you, reply "no" and I will not write again.'
HORIZON = timedelta(hours=1)  # eligible plans this far ahead (the hourly cap makes a longer plan pointless)
SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 465

# ----------------------------------------------------------------------------------------------------------- settings and gates


def settings(cfg: dict | None = None) -> dict:
    return {**SETTINGS["outreach"], **(cfg or {})}


def live(cfg: dict) -> bool:
    """The one test that lets mail leave: mode exactly 'live' and enabled exactly true. A typo, a string or a 1 is a dry run."""
    return cfg.get("mode") == "live" and cfg.get("enabled") is True


def mode_of(cfg: dict) -> str:
    return "live" if live(cfg) else "dry_run"


def blocked(cfg: dict) -> str | None:
    """Why nothing may be written or sent now, or None."""
    if cfg.get("enabled") is not True:
        return "off in settings.yaml (outreach.enabled)"
    if STOP_FILE.exists():
        return "output/outreach.stop exists"
    return None


def notify(title: str, message: str) -> None:
    """A phone push (ntfy, digest.push). Never raises: a push that fails must not stop a pause."""
    try:
        from .digest import push
        push(title, message)
    except Exception as e:  # noqa: BLE001  (the network, ntfy.sh)
        print(f"outreach_send: the phone push failed: {type(e).__name__}")


def mailbox_label(mailbox: str) -> str:
    """A letter instead of the address, for the log and the push."""
    boxes = sorted(accounts())
    return chr(ord("A") + boxes.index(mailbox)) if mailbox in boxes else "?"


# ----------------------------------------------------------------------------------------------------------------- recipients

URL = re.compile(r"https?://[^\s,;)>\"'<]+", re.I)
EMAIL = re.compile(r"[^@\s<>,;\"']+@[^@\s<>,;\"']+\.[A-Za-z]{2,}")
GUESSED = re.compile(r"\b(?:guess\w*|inferred|pattern|permutation|unverified|bought|purchased)\b", re.I)
# a source that is neither the company's own site nor the press: social networks, and email finders and lead databases that sell addresses
BAD_SOURCES = ("linkedin.com", "licdn.com", "facebook.com", "instagram.com", "x.com", "twitter.com", "rocketreach.co", "apollo.io",
               "zoominfo.com", "hunter.io", "lusha.com", "signalhire.com", "contactout.com", "snov.io", "clearbit.com", "leadiq.com",
               "seamless.ai", "adapt.io", "skrapp.io", "voilanorbert.com", "email-format.com", "emailhunter.co")


def field(row: dict, key: str) -> str:
    return (row.get(key) or "").strip()


def source_url(row: dict) -> str | None:
    """The URL the contact was read from: the `source` column, else the last URL in `why` (research founders ends `why` with it)."""
    source = field(row, "source")
    if URL.fullmatch(source):
        return source
    found = URL.findall(field(row, "why"))
    return found[-1] if found else None


def verified(row: dict) -> bool:
    """A Founder or Hiring contact with a well-formed address, a source on the company's own site or in the press (never LinkedIn or a
    seller of addresses), and no note saying the address was guessed."""
    url, email = source_url(row), field(row, "email")
    if field(row, "side") not in drafts.SIDES or not EMAIL.fullmatch(email) or url is None:
        return False
    host = (re.match(r"https?://([^/:?#]+)", url, re.I).group(1) or "").lower()
    if any(host == bad or host.endswith("." + bad) for bad in BAD_SOURCES):
        return False
    return not GUESSED.search(field(row, "why"))


def contact_rows() -> dict[str, dict]:
    """The verified contacts of data/contacts.csv by lower-case address (the first row of an address wins)."""
    path = drafts.CONTACTS
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if verified(r)]
    found: dict[str, dict] = {}
    for r in rows:
        found.setdefault(field(r, "email").lower(), r)
    return found


# The recipient's time zone from the place on their contacts row: the first zone with a place name found as whole words. No match: no email
# (a founder whose hours we cannot know is not mailed). The weekend is Saturday and Sunday except where Friday and Saturday are the days off.
PLACES = {
    "Africa/Cairo": ["cairo", "giza", "alexandria", "egypt"], "Asia/Dubai": ["dubai", "abu dhabi", "uae", "emirates"],
    "Asia/Riyadh": ["riyadh", "jeddah", "saudi"], "Asia/Qatar": ["doha", "qatar"], "Asia/Kuwait": ["kuwait"],
    "Asia/Amman": ["amman", "jordan"], "Europe/Istanbul": ["istanbul", "turkey"],
    "Europe/London": ["london", "uk", "england", "manchester", "edinburgh"], "Europe/Dublin": ["dublin", "ireland"],
    "Europe/Berlin": ["berlin", "munich", "hamburg", "germany"], "Europe/Paris": ["paris", "france"],
    "Europe/Amsterdam": ["amsterdam", "netherlands"], "Europe/Madrid": ["madrid", "barcelona", "spain"],
    "Europe/Rome": ["rome", "milan", "italy"], "Europe/Stockholm": ["stockholm", "sweden"],
    "Europe/Zurich": ["zurich", "switzerland"], "Europe/Warsaw": ["warsaw", "poland"], "Europe/Lisbon": ["lisbon", "portugal"],
    "Europe/Tallinn": ["tallinn", "estonia"], "Europe/Helsinki": ["helsinki", "finland"],
    "Europe/Copenhagen": ["copenhagen", "denmark"], "Europe/Oslo": ["oslo", "norway"],
    "America/New_York": ["new york", "nyc", "boston", "washington", "miami", "atlanta"],
    "America/Chicago": ["chicago", "austin", "texas", "dallas"], "America/Denver": ["denver"],
    "America/Los_Angeles": ["san francisco", "sf", "los angeles", "seattle", "california", "palo alto"],
    "America/Toronto": ["toronto"], "America/Vancouver": ["vancouver"],
    "Asia/Kolkata": ["bangalore", "bengaluru", "mumbai", "delhi", "india"], "Asia/Singapore": ["singapore"],
    "Asia/Jerusalem": ["tel aviv", "israel"], "Africa/Lagos": ["lagos", "nigeria"], "Africa/Nairobi": ["nairobi", "kenya"],
    "Africa/Johannesburg": ["johannesburg", "cape town", "south africa"]}
PLACE_PATTERNS = [(zone, re.compile(r"\b(?:" + "|".join(re.escape(n) for n in names) + r")\b")) for zone, names in PLACES.items()]
FRIDAY_SATURDAY = {"Africa/Cairo", "Asia/Riyadh", "Asia/Qatar", "Asia/Kuwait", "Asia/Amman"}


def zone_for(location: str) -> str | None:
    text = (location or "").lower()
    return next((zone for zone, pattern in PLACE_PATTERNS if pattern.search(text)), None)


def in_working_hours(zone: str, at: datetime, cfg: dict) -> bool:
    local = at.astimezone(ZoneInfo(zone))
    off = (4, 5) if zone in FRIDAY_SATURDAY else (5, 6)
    start, end = cfg["work_hours"]
    return local.weekday() not in off and start <= local.hour < end


# ------------------------------------------------------------------------------------------------------------------ the drafts

# the bans of outreach.py's prompts (HUMAN, BRAND, VOICE: the humanizer skill's tells), checked on the finished text
BANNED = re.compile(
    r"\b(?:let['’]s|here['’]s the thing|honestly|the real question|at its core|i hope (?:this|you)|let me know|crucial|pivotal|valuable"
    r"|showcas\w*|highlight\w*|underscor\w*|enhanc\w*|meticulous\w*|landscape|testament|quietly|serves as|stands as|i am applying"
    r"|i would love the chance|passionate|excited|thrilled|eager|leverag\w*|seamless\w*|cutting-edge|delve|synergy)\b"
    r"|,\s+(?:ensuring|reflecting|highlighting|showcasing|underscoring|demonstrating|enabling|allowing|positioning|fostering|driving"
    r"|contributing)\b|^\s*hi,?\s+i['’]?m omar", re.I | re.M)
LINKS = re.compile(r"https?://|\bwww\.|\butm_|\bbit\.ly|\btinyurl|\bt\.co/", re.I)
ATTACHMENT = re.compile(r"\battach\w*", re.I)
FAKE_REPLY = re.compile(r"^\s*(?:re|fwd?)\s*:", re.I)


def own_words(body: str) -> str:
    """The body without the signature and opt-out lines: what the word limit counts."""
    return "\n".join(line for line in body.splitlines() if line.strip() not in (drafts.SIGNATURE, OPT_OUT)).strip()


def draft_problems(subject: str, body: str, follow: bool = False) -> list[str]:
    """Why this email may not go out; empty when it may. A follow-up (`follow`) may have a "Re:" subject, a first email may not."""
    problems = []
    text = f"{subject}\n{body}"
    if not subject.strip() or not body.strip():
        problems.append("empty")
    if len(own_words(body).split()) > drafts.WORDS:
        problems.append("over the word limit")
    if GAP_WORDS.search(text):
        problems.append("names a gap")
    if BANNED.search(text) or re.search("[—–‘’“”]", text):  # a dash joining clauses, curly quotes: straight quotes only
        problems.append("fails the humanizer check")
    if LINKS.search(text):
        problems.append("has a link")
    if ATTACHMENT.search(text):
        problems.append("mentions an attachment")
    if not follow and FAKE_REPLY.match(subject):
        problems.append("poses as a reply")
    return problems


@dataclass(frozen=True)
class Draft:
    path: Path
    to: str
    subject: str
    body: str  # as written: the text, the name and the signature
    score: int


def read_draft(path: Path) -> Draft | None:
    """A file outreach.py wrote: `To: Name <address>`, `Subject: ...`, a blank line, the body, a blank line, `---`, the notes for Omar."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    head, _, rest = text.partition("\n\n")
    headers = dict(line.split(": ", 1) for line in head.splitlines() if ": " in line)
    to = re.search(r"<([^<>\s]+@[^<>\s]+)>\s*$", headers.get("To", "")) or EMAIL.search(headers.get("To", ""))
    body, _, notes = rest.partition("\n---\n")
    if not to or not headers.get("Subject"):
        return None
    score = re.search(r"\(score (\d+)\)", notes)
    return Draft(path, (to.group(1) if to.groups() else to.group(0)).lower(), headers["Subject"].strip(), body.strip(),
                 int(score.group(1)) if score else 0)


def with_opt_out(body: str) -> str:
    """The body with the signature (added when missing) and exactly one opt-out line, the last line."""
    lines = [line for line in body.rstrip().splitlines() if line.strip() != OPT_OUT]
    text = "\n".join(lines).rstrip()
    if drafts.SIGNATURE not in text:
        text += f"\n{drafts.SIGNATURE}"
    return f"{text}\n\n{OPT_OUT}"


# Follow-ups carry no new claim: a nudge on day 7, a last note on day 14. Written here, not by Claude, so they cost nothing and
# cannot invent a fact; Omar's voice is checked by draft_problems like the first email.
FOLLOW_UPS = {
    1: "{first}, a short follow-up on my note from last week, in case it got buried. If a 15 minute call about the data work at "
       "{company} is useful, I can fit around your week.",
    2: "{first}, one last note from me. If the timing is wrong I will leave it here. If a short call would help, reply and I will "
       "send two times that suit me.",
}


def follow_up_text(number: int, name: str, company: str) -> str:
    first = (name.split() or ["Hello"])[0].strip(",")
    return with_opt_out(f"{FOLLOW_UPS[number].format(first=first, company=company)}\n\n{SENDER_NAME}\n{drafts.SIGNATURE}")


# ------------------------------------------------------------------------------------------------------------------ planning


@dataclass(frozen=True)
class Send:
    mailbox: str
    recipient: str
    name: str
    company: str
    job_id: int | None
    subject: str
    body: str
    followup_no: int
    thread_id: str | None  # None for a first email: its own Message-ID starts the thread
    in_reply_to: str | None
    references: tuple[str, ...]
    at: datetime  # the earliest time it may go


@dataclass(frozen=True)
class Candidate:
    recipient: str
    name: str
    company: str
    zone: str
    subject: str
    body: str
    followup_no: int
    mailbox: str | None  # a follow-up has its mailbox; a first email gets one
    thread_id: str | None
    in_reply_to: str | None
    references: tuple[str, ...]
    stem: str | None  # the draft's file name without .md, to find its job


def gap(seed: str, cfg: dict) -> timedelta:
    """A random wait in gap_minutes, drawn from the seed: the same state gives the same gap at every tick."""
    low, high = cfg["gap_minutes"]
    return timedelta(seconds=random.Random(seed).uniform(low * 60, high * 60))


def paused_mailboxes(conn, now: datetime, cfg: dict | None = None) -> dict[str, datetime]:
    """{mailbox: when its pause ends} for the pauses in force at `now`."""
    return dict(conn.execute("SELECT mailbox, max(until_at) FROM core.outreach_pause WHERE paused_at <= %s AND until_at > %s"
                             " GROUP BY mailbox", (now, now)).fetchall())


def history(conn, mode: str) -> list[tuple]:
    return conn.execute("SELECT id, mailbox, lower(recipient), job_id, thread_id, message_id, sent_at, followup_no, subject"
                        " FROM core.outreach_send WHERE mode = %s ORDER BY sent_at, id", (mode,)).fetchall()


def candidates(conn, now: datetime, cfg: dict, rows: list[tuple], boxes: list[str], skipped: Counter) -> list[Candidate]:
    """Every email that could go: the follow-ups that are due (oldest first), then the first emails (best score first)."""
    known = contact_rows()
    suppressed = {a for (a,) in conn.execute("SELECT lower(address) FROM core.suppression")}
    by_person: dict[str, list[tuple]] = {}
    for r in rows:
        by_person.setdefault(r[2], []).append(r)
    follow, first = [], []
    days, grace = cfg["followup_days"], timedelta(days=cfg["followup_grace_days"])
    for person, sent in by_person.items():
        starter = next((r for r in sent if r[7] == 0), None)
        number = max(r[7] for r in sent) + 1
        if starter is None or number > len(days):
            continue  # no first email on record, or both follow-ups are done: never a third
        due = starter[6] + timedelta(days=days[number - 1])
        if not due <= now < due + grace:
            continue
        row = known.get(person)
        if person in suppressed:
            skipped["suppressed"] += 1
        elif row is None or not starter[8] or starter[1] not in boxes:
            skipped["no contact or mailbox"] += 1
        elif (zone := zone_for(field(row, "location"))) is None:
            skipped["no time zone"] += 1
        else:
            text = follow_up_text(number, field(row, "name"), field(row, "company"))
            if (why := draft_problems("Re: " + starter[8], text, follow=True)):
                skipped["draft"] += 1
                continue
            thread = starter[4]
            follow.append((due, Candidate(person, field(row, "name"), field(row, "company"), zone, "Re: " + re.sub(
                r"^\s*re:\s*", "", starter[8], flags=re.I), text, number, starter[1], thread, sent[-1][5],
                tuple(dict.fromkeys(r[5] for r in sent if r[5])), None)))
    mailed = set(by_person)
    seen: set[str] = set()
    for path in sorted(DRAFTS.glob("*.md")):
        draft = read_draft(path)
        if draft is None:
            continue
        row = known.get(draft.to)
        if row is None:
            skipped["no verified contact"] += 1
        elif draft.to in suppressed:
            skipped["suppressed"] += 1
        elif draft.to in mailed or draft.to in seen:
            skipped["already mailed"] += 1
        elif draft_problems(draft.subject, draft.body):
            skipped["draft"] += 1
        elif (zone := zone_for(field(row, "location"))) is None:
            skipped["no time zone"] += 1
        else:
            seen.add(draft.to)
            first.append((-draft.score, path.stem, Candidate(draft.to, field(row, "name"), field(row, "company"), zone, draft.subject,
                                                             with_opt_out(draft.body), 0, None, None, None, (), path.stem)))
    follow.sort(key=lambda x: (x[0], x[1].recipient))
    first.sort(key=lambda x: (x[0], x[1]))
    return [c for _, c in follow] + [c for *_, c in first]


def job_ids(conn, stems: set[str]) -> dict[str, int]:
    return {s: j for j, company, title in conn.execute("SELECT job_id, company, title FROM core.job").fetchall()
            if (s := slug(company, title)) in stems}


def plan_sends(conn, now: datetime, cfg: dict) -> tuple[list[Send], Counter]:
    """(the sends the rules allow from `now`, why drafts were left out). Nothing is written."""
    skipped: Counter = Counter()
    if blocked(cfg):
        return [], skipped
    passwords = accounts()
    boxes = sorted(passwords)
    if not boxes:
        skipped["no Gmail mailbox in MAILBOXES"] += 1
        return [], skipped
    rows = history(conn, mode_of(cfg))
    today = now.astimezone(CAIRO).date()
    sent_today = Counter(r[1] for r in rows if r[6].astimezone(CAIRO).date() == today)
    total = sum(sent_today.values())
    stamps = [r[6] for r in rows]
    paused = paused_mailboxes(conn, now, cfg)
    # a paused mailbox's share of the day's total is removed, not handed to the others
    total_cap = cfg["daily_total"] - (cfg["daily_total"] // len(boxes)) * sum(m in paused for m in boxes)
    last = rows[-1] if rows else None
    seed = str(last[0]) if last else "0"
    cursor = now if last is None else max(now, last[6] + gap(f"{seed}:0", cfg))
    plan: list[tuple[Candidate, str, datetime]] = []
    planned = Counter()
    for cand in candidates(conn, now, cfg, rows, boxes, skipped):
        if cursor > now + HORIZON or cursor.astimezone(CAIRO).date() != today:
            break
        if sum(cursor - timedelta(hours=1) < t <= cursor for t in stamps + [at for *_, at in plan]) >= cfg["hourly"]:
            break
        if total + len(plan) >= total_cap:
            break
        if not in_working_hours(cand.zone, cursor, cfg):
            skipped["outside their working hours"] += 1
            continue
        usable = [m for m in boxes if paused.get(m, cursor - timedelta(seconds=1)) <= cursor
                  and sent_today[m] + planned[m] < cfg["per_mailbox"]]
        if cand.mailbox is not None:  # a follow-up stays with the mailbox that wrote first: never moved
            usable = [m for m in usable if m == cand.mailbox]
        if not usable:
            skipped["mailbox paused or at its cap"] += 1
            continue
        box = min(usable, key=lambda m: (sent_today[m] + planned[m], hashlib.sha1(f"{cand.recipient}|{m}".encode()).hexdigest()))
        plan.append((cand, box, cursor))
        planned[box] += 1
        cursor += gap(f"{seed}:{len(plan)}", cfg)
    ids = job_ids(conn, {c.stem for c, *_ in plan if c.stem}) if plan else {}
    return [Send(box, c.recipient, c.name, c.company, ids.get(c.stem), c.subject, c.body, c.followup_no, c.thread_id, c.in_reply_to,
                 c.references, at) for c, box, at in plan], skipped


def eligible(conn, now: datetime, cfg: dict | None = None) -> list[Send]:
    """The emails the rules allow from `now` on, the first at `now` (or later when a gap has to pass), each next one a random 2 to 10
    minutes after it, never more than the hourly cap. Read-only. Empty when outreach is off, stopped, or has no Gmail mailbox."""
    return plan_sends(conn, now, settings(cfg))[0]


# ----------------------------------------------------------------------------------------------------------------- the email


def compose(send: Send, message_id: str, now: datetime) -> EmailMessage:
    """The plain-text email: no attachment, no HTML, no tracking, no extra headers beyond the thread's."""
    msg = EmailMessage()
    msg["From"] = formataddr((SENDER_NAME, send.mailbox))
    msg["To"] = formataddr((send.name, send.recipient))
    msg["Subject"] = send.subject
    msg["Date"] = format_datetime(now)
    msg["Message-ID"] = message_id
    if send.in_reply_to:
        msg["In-Reply-To"] = send.in_reply_to
        msg["References"] = " ".join(send.references or (send.in_reply_to,))
    msg.set_content(send.body)
    return msg


def eml_path(send: Send) -> Path:
    name = re.sub(r"[^A-Za-z0-9@._+-]", "_", send.recipient)
    return DRAFTS / "outbox" / f"{name}-{send.followup_no + 1}.eml"


def add_suppression(conn, address: str, reason: str, now: datetime) -> bool:
    return conn.execute("INSERT INTO core.suppression (address, reason, added_at) VALUES (%s, %s, %s) ON CONFLICT (address) DO NOTHING",
                        (address.lower(), reason, now)).rowcount == 1


def deliver(conn, send: Send, now: datetime, cfg: dict) -> bool:
    """Write (dry run) or send (live) one email and record it. The row is written first and, live, committed before the mail leaves:
    a crash can lose an email, never send one twice. False when nothing went."""
    if live(cfg) and STOP_FILE.exists():
        return False
    domain = send.mailbox.rpartition("@")[2]
    message_id = make_msgid(domain=domain)
    thread = send.thread_id or message_id
    inserted = conn.execute(
        "INSERT INTO core.outreach_send (mailbox, recipient, job_id, thread_id, message_id, sent_at, mode, followup_no, subject)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING id",
        (send.mailbox, send.recipient.lower(), send.job_id, thread, message_id, now, mode_of(cfg), send.followup_no,
         send.subject)).fetchone()
    if inserted is None:
        return False
    msg = compose(Send(**{**send.__dict__, "thread_id": thread}), message_id, now)
    if not live(cfg):
        path = eml_path(send)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(msg.as_bytes())
        return True
    password = accounts().get(send.mailbox)
    conn.commit()
    attempted = False
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=60) as smtp:
            smtp.login(send.mailbox, password)
            attempted = True
            smtp.send_message(msg)
    except smtplib.SMTPRecipientsRefused as e:
        if all(code >= 500 for code, _ in e.recipients.values()):  # the address does not exist: never again
            add_suppression(conn, send.recipient, "bounce", now)
        undo(conn, inserted[0])
        print("outreach_send: a recipient was refused, left out")
        return False
    except (smtplib.SMTPException, OSError) as e:
        if attempted:  # it may have gone: keep the row so it is never sent twice
            print(f"outreach_send: the send may or may not have gone ({type(e).__name__}); recorded, not retried")
            return True
        undo(conn, inserted[0])
        print(f"outreach_send: could not send ({type(e).__name__}), tried again next tick")
        return False
    return True


def undo(conn, row_id: int) -> None:
    conn.execute("DELETE FROM core.outreach_send WHERE id = %s", (row_id,))
    conn.commit()


def run(conn, now: datetime, cfg: dict | None = None) -> list[Send]:
    """One tick: the email that is due, if any (never more than one: the gaps are real). Dry run writes it to
    output/outreach/outbox/<recipient>-<n>.eml; live sends it. Returns what it did."""
    cfg = settings(cfg)
    if why := blocked(cfg):
        print(f"outreach_send: {why}, nothing sent")
        return []
    pause_unhealthy(conn, now, cfg)
    plan, skipped = plan_sends(conn, now, cfg)
    done = [s for s in plan[:1] if s.at <= now and deliver(conn, s, now, cfg)]
    left = f"; left out: {', '.join(f'{n} {why}' for why, n in sorted(skipped.items()))}" if skipped else ""
    verb = "sent" if live(cfg) else "written to the outbox"
    print(f"outreach_send ({mode_of(cfg)}): {len(done)} {verb}, {max(len(plan) - len(done), 0)} more allowed this hour{left}")
    return done


# ------------------------------------------------------------------------------------------------- replies, bounces, mailbox health

BOUNCE_FROM = re.compile(r"mailer-daemon|postmaster|mail delivery subsystem", re.I)
BOUNCE_SUBJECT = re.compile(r"delivery status notification|undeliverable|mail delivery failed|returned mail|delivery failure"
                            r"|failure notice|couldn['’]?t be delivered|wasn['’]?t delivered|not delivered", re.I)
COMPLAINT = re.compile(r"spam complaint|reported (?:this |your |the )?(?:message|email|mail)?\s*(?:as|for) spam|marked (?:this|it|your \w+)"
                       r" as spam|abuse report|feedback loop|this is spam", re.I)
AUTO_REPLY = re.compile(r"^\s*(?:automatic reply|auto[- ]?reply|auto:|out of (?:the )?office)|out of (?:the )?office|automatic reply", re.I)
NEW_CONTACT = re.compile(r"no longer (?:with|at|works?)|has left|left the company|(?:please )?(?:contact|reach out to|email|write to)\s+\S+@", re.I)
UNSUBSCRIBE = re.compile(r"unsubscribe|remove me|take me off|stop (?:emailing|writing|contacting|sending)|do not (?:contact|email|write)"
                         r"|don['’]?t (?:contact|email|write)", re.I)
REFUSAL = re.compile(r"^\W*no\b|not interested|no thanks|no thank you|not hiring|not looking|not a fit|please stop", re.I)


def own_text(text: str) -> str:
    """A reply's own words: the lines it quotes (`>`) and everything from "On ... wrote:" down are our email, not their answer."""
    kept = []
    for line in (text or "").splitlines():
        if re.match(r"\s*on .{0,120}wrote:?\s*$", line, re.I) or re.match(r"\s*-{2,}\s*original message", line, re.I):
            break
        if not line.lstrip().startswith(">"):
            kept.append(line)
    return "\n".join(kept).strip()


def classify_reply(sender: str, subject: str, text: str, known: set[str]) -> tuple[str, str] | None:
    """(reason, the address it is about) for a row of mail, or None: a bounce (the failed address is named in the text), a spam complaint,
    or an answer from someone we mailed ('unsubscribe', 'no', 'out_of_office' only when it names a new contact, else 'reply')."""
    mentioned = [a.lower() for a in EMAIL.findall(f"{subject}\n{text}") if a.lower() in known]
    if BOUNCE_FROM.search(sender) or BOUNCE_SUBJECT.search(subject):
        return ("bounce", mentioned[0]) if mentioned else None
    if COMPLAINT.search(f"{subject}\n{text}"):
        address = sender if sender in known else (mentioned[0] if mentioned else None)
        return ("complaint", address) if address else None
    if sender not in known:
        return None
    words = own_text(text)
    if AUTO_REPLY.search(subject) or AUTO_REPLY.search(words):
        return ("out_of_office", sender) if NEW_CONTACT.search(words) else None
    if UNSUBSCRIBE.search(f"{subject}\n{words}"):
        return "unsubscribe", sender
    if REFUSAL.search(words):
        return "no", sender
    return "reply", sender


def active_pause(conn, mailbox: str, now: datetime) -> bool:
    return conn.execute("SELECT 1 FROM core.outreach_pause WHERE mailbox = %s AND paused_at <= %s AND until_at > %s",
                        (mailbox, now, now)).fetchone() is not None


def pause(conn, mailbox: str, reason: str, now: datetime, cfg: dict) -> bool:
    """Pause the mailbox for pause_days and say so on the phone. False when it is already paused."""
    if active_pause(conn, mailbox, now):
        return False
    until = now + timedelta(days=cfg["pause_days"])
    conn.execute("INSERT INTO core.outreach_pause (mailbox, reason, paused_at, until_at) VALUES (%s, %s, %s, %s)",
                 (mailbox, reason, now, until))
    notify("Outreach mailbox paused",
           f"Mailbox {mailbox_label(mailbox)} paused until {until:%Y-%m-%d}: {reason}. Its volume is not moved to the other mailboxes.")
    print(f"outreach_send: mailbox {mailbox_label(mailbox)} paused until {until:%Y-%m-%d} ({reason})")
    return True


def pause_unhealthy(conn, now: datetime, cfg: dict | None = None) -> list[str]:
    """Pause each mailbox whose bounce rate (recipients that bounced over recipients mailed, live, since the later of bounce_window_days
    ago and the end of its last pause) is over bounce_limit. Returns the mailboxes paused now."""
    cfg = settings(cfg)
    newly = []
    for mailbox in sorted(accounts()):
        if active_pause(conn, mailbox, now):
            continue
        ended = conn.execute("SELECT max(until_at) FROM core.outreach_pause WHERE mailbox = %s AND until_at <= %s",
                             (mailbox, now)).fetchone()[0]
        since = max(now - timedelta(days=cfg["bounce_window_days"]), ended) if ended else now - timedelta(days=cfg["bounce_window_days"])
        mailed, bounced = conn.execute(
            "SELECT count(DISTINCT s.recipient), count(DISTINCT s.recipient) FILTER (WHERE p.reason = 'bounce')"
            " FROM core.outreach_send s LEFT JOIN core.suppression p ON p.address = lower(s.recipient)"
            " WHERE s.mailbox = %s AND s.mode = 'live' AND s.sent_at >= %s", (mailbox, since)).fetchone()
        if mailed and bounced / mailed > cfg["bounce_limit"]:
            if pause(conn, mailbox, f"bounce rate {bounced} of {mailed} recipients over {cfg['bounce_limit']:.0%}", now, cfg):
                newly.append(mailbox)
    return newly


def suppress_from_replies(conn, rows: list[dict], now: datetime | None = None, cfg: dict | None = None) -> dict:
    """Read mail (the dicts read_mailbox returns, or rows of core.application_email: sender, subject, received_at, mailbox and, when
    there, the text) and add to core.suppression every address that answered, refused, unsubscribed, bounced or complained after we
    mailed it. A complaint pauses the mailbox it came to; a bounce counts toward its bounce rate. Idempotent. Returns counts."""
    now, cfg = now or datetime.now(timezone.utc), settings(cfg)
    boxes = set(accounts())
    first_at: dict[str, datetime] = {}
    last_box: dict[str, str] = {}
    for _id, mailbox, recipient, _job, _thread, _mid, sent_at, _n, _subject in history_all(conn):
        first_at[recipient] = min(sent_at, first_at.get(recipient, sent_at))
        last_box[recipient] = mailbox
    known = set(first_at)
    out = {"suppressed": 0, "bounces": 0, "complaints": 0, "paused": []}
    for row in sorted(rows, key=lambda r: r["received_at"]):
        sender = parseaddr(row.get("sender") or "")[1].lower()
        found = classify_reply(sender, row.get("subject") or "", row.get("text") or row.get("body") or "", known)
        if found is None:
            continue
        reason, address = found
        if row["received_at"] < first_at[address] or not add_suppression(conn, address, reason, now):
            continue
        out["suppressed"] += 1
        if reason == "bounce":
            out["bounces"] += 1
        elif reason == "complaint":
            out["complaints"] += 1
            mailbox = row.get("mailbox") if row.get("mailbox") in boxes else last_box[address]
            if pause(conn, mailbox, "spam complaint", now, cfg):
                out["paused"].append(mailbox)
    for mailbox in pause_unhealthy(conn, now, cfg):
        if mailbox not in out["paused"]:
            out["paused"].append(mailbox)
    print(f"outreach_send: {out['suppressed']} addresses suppressed ({out['bounces']} bounces, {out['complaints']} complaints), "
          f"{len(out['paused'])} mailboxes paused")
    return out


def history_all(conn) -> list[tuple]:
    """Every email we wrote, either mode: a reply to a dry run's email cannot exist, but a recipient who answered once is never mailed cold."""
    return conn.execute("SELECT id, mailbox, lower(recipient), job_id, thread_id, message_id, sent_at, followup_no, subject"
                        " FROM core.outreach_send ORDER BY sent_at, id").fetchall()
