"""Every email of every Gmail account in MAILBOXES, whole, in core.mail: All Mail and Spam, read-only (BODY.PEEK: nothing is
marked read, moved or deleted). No keyword picks them: what an email needs from you is written after reading it (by Claude
in a session, from `unreviewed`, saved with `save_review`), and the tracker's Inbox page lists it.

    python -m job_radar.mail_inbox collect [days]      store the last `days` days (14) of every folder
    python -m job_radar.mail_inbox export FILE         the emails not read yet, one JSON per line
    python -m job_radar.mail_inbox import FILE         what each needs from you, one JSON per line (mailbox, message_id, action, urgency, due)
"""
from __future__ import annotations

import email
import email.policy
import hashlib
import imaplib
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup

from .applications import accounts, clean
from .db import connect

# mail about tools, code, rides and courses is not about a job: its action goes under "Other" in the Inbox
NOT_JOB = re.compile(r"anthropic|github|vercel|apify|neon\.tech|didi|clay|vibe prospecting|explorium|datapill|fennec|notion|grok|cloudflare|"
                     r"google|dmarc|hevy|skool|udemy|grammarly|openrouter|brevo|dribbble|cheaperinference|reddit|postman|job-radar|client-radar", re.I)


def topic_of(sender: str, subject: str) -> str:
    """'other' for a tool, code or service mail; 'job' for the rest: the Inbox's To do lists job, interview and assessment mail only."""
    return "other" if NOT_JOB.search(f"{sender} {subject}") else "job"


FOLDERS = {"All Mail": '"[Gmail]/All Mail"', "Spam": '"[Gmail]/Spam"'}
BODY_CHARS = 40_000  # a whole email; only a pasted newsletter is longer


def body_with_links(msg) -> str:
    """The email's text, each link as 'text (url)': the form or test link is what you act on."""
    part = msg.get_body(preferencelist=("html", "plain"))
    if part is None:
        return ""
    text = part.get_content()
    if part.get_content_type() == "text/html":
        soup = BeautifulSoup(text, "html.parser")
        for tag in soup(["style", "script", "head"]):
            tag.decompose()
        for a in soup.find_all("a", href=True):
            if a["href"].startswith("http"):
                a.replace_with(f"{a.get_text(' ', strip=True) or 'link'} ({a['href']})")
        text = soup.get_text(" ")
    return " ".join(text.split())[:BODY_CHARS]


def parse(raw: bytes, mailbox: str, folder: str) -> dict | None:
    """One email as a core.mail row; None for the radar's own digests (they are not mail to you)."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    subject = clean(str(msg["Subject"] or ""))
    if subject.startswith("Job radar"):
        return None
    try:
        received = parsedate_to_datetime(str(msg["Date"]))
        received = received if received.tzinfo else received.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        received = datetime.now(timezone.utc)
    message_id = str(msg["Message-ID"] or "").strip() or "sha1:" + hashlib.sha1(raw[:20_000]).hexdigest()
    return {"mailbox": mailbox, "message_id": message_id, "folder": folder, "received_at": received,
            "sender": clean(str(msg["From"] or "")), "subject": subject, "body": body_with_links(msg)}


def store(conn, mails: list[dict]) -> int:
    """Insert the emails not stored yet (a mailbox's message id is one row, whichever folder it is read from); returns how many."""
    new = 0
    for m in mails:
        new += conn.execute(
            "INSERT INTO core.mail (mailbox, message_id, folder, received_at, sender, subject, body)"
            " VALUES (%(mailbox)s, %(message_id)s, %(folder)s, %(received_at)s, %(sender)s, %(subject)s, %(body)s)"
            " ON CONFLICT (mailbox, message_id) DO NOTHING", m).rowcount
    return new


def collect(conn, days: int = 14) -> None:
    since = (date.today() - timedelta(days=days)).strftime("%d-%b-%Y")
    for user, password in accounts().items():
        found = {}
        with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
            imap.login(user, password)
            for folder, name in FOLDERS.items():
                if imap.select(name, readonly=True)[0] != "OK":
                    print(f"WARNING mail_inbox: {user}: folder {folder} could not be opened")
                    continue
                for uid in imap.uid("search", None, "SINCE", since)[1][0].split():
                    typ, data = imap.uid("fetch", uid, "(BODY.PEEK[])")
                    if typ == "OK" and data and isinstance(data[0], tuple):
                        if mail := parse(data[0][1], user, folder):
                            found.setdefault(mail["message_id"], mail)
        print(f"mail_inbox: {user}: {len(found)} emails read, {store(conn, list(found.values()))} new")
    conn.commit()


def unreviewed(conn) -> list[dict]:
    """The emails not read for what they need from you yet, oldest first."""
    cur = conn.execute("SELECT mailbox, message_id, folder, received_at, sender, subject, body FROM core.mail"
                       " WHERE reviewed_at IS NULL ORDER BY received_at")
    return [dict(zip(("mailbox", "message_id", "folder", "received_at", "sender", "subject", "body"), row)) for row in cur.fetchall()]


def save_review(conn, results: list[dict]) -> int:
    """Save what each email needs from you: action (empty or None: nothing needed), urgency, due (YYYY-MM-DD or None)."""
    for r in results:
        conn.execute("UPDATE core.mail SET action = nullif(trim(%s), ''), urgency = %s, due = %s, reviewed_at = now(),"
                     " topic = CASE WHEN sender ~* %s OR subject ~* %s THEN 'other' ELSE 'job' END"
                     " WHERE mailbox = %s AND message_id = %s",
                     (r.get("action"), r.get("urgency"), r.get("due"), NOT_JOB.pattern, NOT_JOB.pattern, r["mailbox"], r["message_id"]))
    return len(results)


def set_done(conn, mailbox: str, message_id: str) -> None:
    """You did what the email asked: the Inbox page's Done button."""
    conn.execute("UPDATE core.mail SET done = true WHERE mailbox = %s AND message_id = %s", (mailbox, message_id))


def main() -> None:
    command, *rest = sys.argv[1:] or ["collect"]
    with connect() as conn:
        if command == "collect":
            collect(conn, int(rest[0]) if rest else 14)
        elif command == "export":
            with open(rest[0], "w", encoding="utf-8") as out:
                for m in unreviewed(conn):
                    out.write(json.dumps({**m, "received_at": m["received_at"].isoformat()}, ensure_ascii=False) + "\n")
        elif command == "import":
            results = [json.loads(line) for line in open(rest[0], encoding="utf-8") if line.strip()]
            print(f"mail_inbox: {save_review(conn, results)} emails reviewed")
        else:
            raise SystemExit(__doc__)
        conn.commit()


if __name__ == "__main__":
    main()
