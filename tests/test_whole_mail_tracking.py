"""track_applications reads every stored email too (core.mail: All Mail and Spam, whole), not only Gmail's subject search:
"Talan - Gracias por su solicitud", "beehiiv | thank you for considering us" and The Job Portal's form (2026-11-09 to 12)
have no application word in their subject and were never tracked."""
from datetime import datetime, timezone

from job_radar import applications, mail_inbox
from test_steps import RolledBack, conn  # noqa: F401  (conn is the warehouse fixture)

NOW = datetime.now(timezone.utc)


def mail(conn, n: int, sender: str, subject: str, body: str = "Hi Omar", folder: str = "All Mail", mailbox: str = "zz@gmail.com") -> str:
    message_id = f"<zz-whole-{n}@x>"
    mail_inbox.store(conn, [{"mailbox": mailbox, "message_id": message_id, "folder": folder, "received_at": NOW, "sender": sender,
                             "subject": subject, "body": body}])
    return message_id


def ids(found: dict) -> set[str]:
    return {m["message_id"] for mails in found.values() for m in mails}


def test_every_stored_email_that_is_no_alert_and_no_tool_mail_is_read_spam_too(conn):
    talan = mail(conn, 1, "Talan <notification@smartrecruiters.talan.com>", "Talan - Gracias por su solicitud",
                 "Muchas gracias por aplicar. Access My Application (https://link.smartrecruiters.talan.com/c/" + "e" * 300 + ")")
    spam = mail(conn, 2, "Isidora <notifications@app.bamboohr.com>", "beehiiv | thank you for considering us", folder="Spam")
    alert = mail(conn, 3, "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>", "data engineer: 12 new jobs")
    tool = mail(conn, 4, "Omar <notifications@github.com>", "[omarshalabyy1/job-radar] Run failed")
    digest = mail(conn, 5, "Indeed <alert@jobalert.indeed.com>", "Salesforce Business Analyst at Capgemini")
    found = applications.whole_mail(conn, set())
    assert {talan, spam} <= ids(found) and not {alert, tool, digest} & ids(found)
    one = next(m for m in found["zz@gmail.com"] if m["message_id"] == talan)
    # shaped like a leftover of the Gmail search; a link keeps its text and its host, not its tracking tail
    assert one["to"] == "zz@gmail.com" and one["sender"].startswith("Talan") and one["received_at"] == NOW
    assert "Access My Application (link.smartrecruiters.talan.com)" in one["text"] and "eeee" not in one["text"]


def test_an_email_tracked_before_is_not_read_again(conn):
    seen = mail(conn, 6, "ERP Suites <noreply@applytojob.com>", "Omar, we've received your resume")
    stored = mail(conn, 7, "HR <hr@acme.example>", "Welcome")
    found = ids(applications.whole_mail(conn, {seen}))
    assert seen not in found and stored in found


def test_invisible_filler_is_dropped_so_the_message_is_what_is_read(conn):
    """Indeed's "New Message from <employer>" opens with hundreds of zero-width characters: they filled the 2,000 read."""
    padded = mail(conn, 9, "EGYNEERING <no-reply@indeed.com>", "New Message from EGYNEERING", "Log in " + "\u034f\u00ad " * 1500 + "to reply")
    text = next(m["text"] for m in applications.whole_mail(conn, set())["zz@gmail.com"] if m["message_id"] == padded)
    assert text == "Log in to reply"


def test_track_applications_classifies_and_stores_them(conn, monkeypatch):
    form = mail(conn, 8, "The Job Portal <hello@thejobportal.co.uk>", "Junior AI & Automation Specialist - The Job Portal",
                "Thank you for applying. Complete our form (https://updatecentre.typeform.com/to/qVIcZqi3)")
    asked = []
    monkeypatch.setitem(applications.SETTINGS.setdefault("features", {}), "track_applications", True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(applications, "accounts", lambda: {"zz@gmail.com": "p"})
    monkeypatch.setattr(applications, "read_mailbox", lambda *a: ([], [], 1))
    monkeypatch.setattr(applications, "ask_claude", lambda leftovers, cfg, me: asked.extend(leftovers) or [
        {**m, "kind": "applied", "company": "The Job Portal", "title": "Junior AI & Automation Specialist"} for m in leftovers])
    monkeypatch.setattr(applications, "interview_prep", lambda conn, mails: (0, 0))
    monkeypatch.setattr(applications, "follow_up_drafts", lambda conn: (0, 0))
    applications.track_applications(RolledBack(conn))
    assert form in {m["message_id"] for m in asked}
    assert conn.execute("SELECT kind, company, mailbox FROM core.application_email WHERE message_id = %s", (form,)).fetchone() == (
        "applied", "The Job Portal", "zz@gmail.com")
