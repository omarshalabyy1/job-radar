"""One-off: the confirmation emails stored with no job title or no company (their subject names none) are read again in
full, and what the email itself says is filled in. Then check_applications can add them to the tracker.

    python scripts/retitle_applications.py          # reads Gmail (read-only) for a body core.mail no longer holds
"""
from job_radar.applications import BODY_CHARS, LINK, PADDING, accounts, ask_claude, fetch_body
from job_radar.config import SETTINGS
from job_radar.db import connect

ROWS = ("SELECT e.message_id, e.mailbox, e.sender, e.subject, e.company, e.title, m.body FROM core.application_email e"
        " LEFT JOIN core.mail m ON m.message_id = e.message_id AND m.mailbox = e.mailbox"
        " WHERE e.kind = 'applied' AND e.job_id IS NULL AND e.mailbox IS NOT NULL AND (e.title IS NULL OR e.company IS NULL)")


def main() -> None:
    boxes, filled, read = accounts(), 0, 0
    with connect() as conn:
        rows = conn.execute(ROWS).fetchall()
        conn.rollback()  # no transaction stays open while Gmail and Claude are read
        for mailbox in sorted({r[1] for r in rows} & set(boxes)):
            mails = []
            for message_id, _, sender, subject, company, title, body in (r for r in rows if r[1] == mailbox):
                text = PADDING.sub("", LINK.sub(r"(\1)", body))[:BODY_CHARS] if body else fetch_body(mailbox, boxes[mailbox], message_id)
                if text:
                    mails.append({"message_id": message_id, "sender": sender, "subject": subject, "to": mailbox, "text": text,
                                  "kind": "applied", "company": company, "title": title})
            read += len(mails)
            for m in ask_claude(mails, SETTINGS.get("claude") or {}, mailbox):
                filled += conn.execute("UPDATE core.application_email SET company = %s, title = %s WHERE message_id = %s"
                                       " AND (company IS DISTINCT FROM %s OR title IS DISTINCT FROM %s)",
                                       (m["company"], m["title"], m["message_id"], m["company"], m["title"])).rowcount
            conn.commit()
    print(f"retitle_applications: {len(rows)} confirmations with no title or company, {read} read in full, {filled} filled in")


if __name__ == "__main__":
    main()
