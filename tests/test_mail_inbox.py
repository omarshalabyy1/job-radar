"""core.mail: every email of every mailbox folder, whole, with its links; what it needs from you is written after reading it."""
from email.message import EmailMessage

from job_radar import mail_inbox
from test_steps import conn  # noqa: F401  (conn is the warehouse fixture)


def raw(subject="Complete your form", html=True, message_id="<m1@x>", sender="Hiring <hr@acme.com>") -> bytes:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"], msg["Message-ID"], msg["Date"] = sender, "me@gmail.com", subject, message_id, "Mon, 09 Nov 2026 10:00:00 +0000"
    if html:
        msg.set_content("plain version")
        msg.add_alternative('<html><body><p>Please <a href="https://forms.acme.com/apply?id=7">complete our form</a> today.</p></body></html>',
                            subtype="html")
    else:
        msg.set_content("Plain text only, no html.")
    return msg.as_bytes()


def test_parse_keeps_the_whole_text_and_the_links_of_an_html_mail():
    mail = mail_inbox.parse(raw(), "me@gmail.com", "All Mail")
    assert mail["subject"] == "Complete your form" and mail["folder"] == "All Mail" and mail["message_id"] == "<m1@x>"
    assert "Please complete our form (https://forms.acme.com/apply?id=7) today." in mail["body"]
    assert mail["received_at"].year == 2026 and "hr@acme.com" in mail["sender"]


def test_parse_reads_a_plain_text_mail_and_skips_our_own_radar_digest():
    assert mail_inbox.parse(raw(html=False), "me@gmail.com", "Spam")["body"] == "Plain text only, no html."
    assert mail_inbox.parse(raw(subject="Job radar · Egypt · 14 new jobs"), "me@gmail.com", "All Mail") is None


def test_store_keeps_one_row_per_mail_and_the_review_is_saved_once_read(conn):
    mail = mail_inbox.parse(raw(message_id="<store-test@x>"), "me@gmail.com", "Spam")
    assert mail_inbox.store(conn, [mail, mail]) == 1 and mail_inbox.store(conn, [mail]) == 0
    assert [m["message_id"] for m in mail_inbox.unreviewed(conn) if m["message_id"] == "<store-test@x>"] == ["<store-test@x>"]
    mail_inbox.save_review(conn, [{"mailbox": "me@gmail.com", "message_id": "<store-test@x>", "action": "Fill the form", "urgency": "today",
                                   "due": None}])
    assert not [m for m in mail_inbox.unreviewed(conn) if m["message_id"] == "<store-test@x>"]
    assert conn.execute("SELECT action, urgency, reviewed_at IS NOT NULL, done FROM core.mail WHERE message_id = '<store-test@x>'").fetchone() == (
        "Fill the form", "today", True, False)
    mail_inbox.set_done(conn, "me@gmail.com", "<store-test@x>")
    assert conn.execute("SELECT done FROM core.mail WHERE message_id = '<store-test@x>'").fetchone() == (True,)


def test_topic_of_tells_job_mail_from_everything_else():
    job = [("Valeo Recruitment Team <support@hackerrankforwork.com>", "Your HackerRank Valeo English Exam - Speaking Invitation"),
           ("The Job Portal Recruitment Team <hello@thejobportal.co.uk>", "Junior AI & Automation Specialist - The Job Portal"),
           ("Himalayas <hi@himalayas.app>", "Did you hear back from All Your BI?"),
           ("Mariam Ali <mariam.ali13@tp.com>", "Invitation: Omar Sabry - BI Developer"),
           ("no-reply@telusinternational.ai", "Action required: complete your job application")]
    other = [("Anthropic <no-reply@mail.anthropic.com>", 'Action required: Your API key "omniroute" expires in 7 days'),
             ("Omar Shalaby <notifications@github.com>", "[omarshalabyy1/job-radar] Run failed: daily-backup - main"),
             ("DiDi <noreply@didiglobal.com>", "Unpaid ride"), ("Vercel <notifications@vercel.com>", "Failed production deployment"),
             ("DataPill <admin@datapill.net>", "Reminder: Session 18"), ("Omar <me@gmail.com>", "job-radar FAILED: publish_applications"),
             ("Omar <me@gmail.com>", "job-radar: 4 sources silent or refusing in this run"), ("Apify <hello@apify.com>", "Maximum platform usage per month")]
    assert [mail_inbox.topic_of(s, subject) for s, subject in job] == ["job"] * len(job)
    assert [mail_inbox.topic_of(s, subject) for s, subject in other] == ["other"] * len(other)
