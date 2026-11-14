"""check_applications adds an application your mailbox confirms and the tracker has no job for (Omar, 2026-11-14: "it should
be in the tracker"), from what the email says, and invents nothing."""
from datetime import datetime, timezone

from job_radar.check_applications import check_applications, linkedin_posting
from test_steps import RolledBack, conn  # noqa: F401  (conn is the warehouse fixture)

NOW = datetime.now(timezone.utc)
BODY = ("Your application was sent to Zzmailb link (https://www.linkedin.com/comm/jobs/view/4490514018/?trk=x&a=b) Business Intelligence "
        "Analyst Zzmailb · Melville, NY (Hybrid) (https://www.linkedin.com/comm/jobs/view/4490514018/?trk=y) Applied on November 14, 2026")


def email(conn, n, kind, company, title=None, subject="news", body=None):
    message_id = f"<zz-mailonly-{n}>"
    conn.execute("INSERT INTO core.application_email (message_id, received_at, sender, subject, company, title, kind, mailbox)"
                 " VALUES (%s, %s, 'hr@zz.example', %s, %s, %s, %s, 'zz@gmail.com')", (message_id, NOW, subject, company, title, kind))
    if body:
        conn.execute("INSERT INTO core.mail (mailbox, message_id, folder, received_at, sender, subject, body)"
                     " VALUES ('zz@gmail.com', %s, 'All Mail', %s, 'x', %s, %s)", (message_id, NOW, subject, body))


def jobs(conn):
    return conn.execute("SELECT j.title, j.company, j.location, j.job_url, j.source, a.status FROM core.job j JOIN core.application a"
                        " USING (job_id) WHERE j.company LIKE 'Zzmail%' ORDER BY 1").fetchall()


def test_linkedins_email_gives_the_title_the_place_and_the_posting():
    assert linkedin_posting(BODY, "Zzmailb") == ("Business Intelligence Analyst", "Melville, NY (Hybrid)",
                                                "https://www.linkedin.com/jobs/view/4490514018/")
    assert linkedin_posting("Thank you for applying", "Zzmail") is None


def test_an_application_known_from_emails_alone_gets_its_job_and_status_once(conn):
    email(conn, 1, "applied", "Zzmaila", "Data Engineer")
    email(conn, 2, "rejected", "Zzmaila", "Data Engineer")
    email(conn, 3, "applied", "Zzmailb", subject="Omar, your application was sent to Zzmailb", body=BODY)
    email(conn, 4, "applied", "Zzmailc")  # no title anywhere: left out
    check_applications(RolledBack(conn))
    first = jobs(conn)
    assert [(r[0], r[1], r[5]) for r in first] == [("Business Intelligence Analyst", "Zzmailb", "applied"),
                                                   ("Data Engineer", "Zzmaila", "rejected")]
    assert first[0][2:5] == ("Melville, NY (Hybrid)", "https://www.linkedin.com/jobs/view/4490514018/", "applied")
    # no posting link: the email itself, without the mailbox (job_url is published to Neon)
    assert first[1][3].startswith("https://mail.google.com/mail/u/#search/rfc822msgid") and "gmail.com" not in first[1][3]
    assert conn.execute("SELECT count(*) FROM core.job WHERE company LIKE 'Zzmail%' AND in_scope").fetchone() == (0,)
    assert conn.execute("SELECT count(*) FROM core.application_email WHERE message_id LIKE '<zz-mailonly-%' AND job_id IS NOT NULL"
                        ).fetchone() == (3,)
    check_applications(RolledBack(conn))
    assert jobs(conn) == first
