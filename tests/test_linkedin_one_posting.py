"""One LinkedIn posting is one job, whatever each source calls the company. On 2026-11-14 posting 4490317795 was two jobs:
LinkedIn's own page names the company 'Systems Limited - Egypt' (job_key '... | systemsegypt'), the job alert email names it
'Systems Limited' ('... | systems'). The first was applied to; the second stayed in the tracker's "Apply next"."""

from job_radar import steps
from tests.test_steps import RolledBack, conn  # noqa: F401  (the rolled-back warehouse connection)

PAGE = "https://eg.linkedin.com/jobs/view/data-engineer-at-zzlinkco-egypt-999000111"
ALERT = "https://www.linkedin.com/jobs/view/999000111"


def posting(conn, company: str, url: str) -> None:  # noqa: F811
    conn.execute("INSERT INTO raw.job_posting (run_date, source, title, company, location, searched_for, job_url, payload,"
                 " last_seen) VALUES (current_date, 'test', 'Data Engineer', %s, 'Cairo', 'Egypt', %s, '{}', current_date)",
                 (company, url))


def jobs(conn) -> list:  # noqa: F811
    return conn.execute("SELECT company, job_url FROM core.job WHERE job_url LIKE '%%999000111'").fetchall()


def test_the_page_and_the_alert_of_one_posting_are_one_job(conn):  # noqa: F811
    posting(conn, "Zzlinkco - Egypt", PAGE)
    posting(conn, "Zzlinkco", ALERT)
    steps.transform(RolledBack(conn))
    assert jobs(conn) == [("Zzlinkco - Egypt", PAGE)]


def test_an_alert_of_a_posting_already_a_job_adds_no_second_job(conn):  # noqa: F811
    posting(conn, "Zzlinkco - Egypt", PAGE)
    steps.transform(RolledBack(conn))
    conn.execute("UPDATE raw.job_posting SET last_seen = current_date - %s WHERE job_url = %s", (steps.DAYS + 1, PAGE))
    posting(conn, "Zzlinkco", ALERT)  # the page's posting has left the window: only the job in core.job knows the id
    steps.transform(RolledBack(conn))
    assert jobs(conn) == [("Zzlinkco - Egypt", PAGE)]
    steps.transform(RolledBack(conn))  # and a rerun changes nothing
    assert jobs(conn) == [("Zzlinkco - Egypt", PAGE)]


def test_of_two_jobs_already_stored_for_one_posting_the_one_you_applied_to_takes_its_postings(conn):  # noqa: F811
    posting(conn, "Zzlinkco - Egypt", PAGE)
    steps.transform(RolledBack(conn))
    cols = ", ".join(c for (c,) in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'core' AND table_name = 'job'"
        " AND column_name NOT IN ('job_id', 'job_key', 'job_url', 'company') AND is_generated = 'NEVER'"))
    (twin,) = conn.execute(  # the alert's job, stored before this rule, and the one applied to
        f"INSERT INTO core.job (job_key, job_url, company, {cols}) SELECT 'data engineer | zzlinkco', %s, 'Zzlinkco', {cols}"
        " FROM core.job WHERE job_url = %s RETURNING job_id", (ALERT, PAGE)).fetchone()
    conn.execute("INSERT INTO core.application (job_id, status) VALUES (%s, 'applied')", (twin,))
    steps.transform(RolledBack(conn))
    assert jobs(conn) == [("Zzlinkco", ALERT)]  # the page's posting is the applied job's now; its own job, not acted on, leaves
