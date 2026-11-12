"""A job whose posting date is older than KEEP_DAYS is not a new job even when the radar saw it for the first time today:
company boards list every open role with its original date (Canonical's was 2021). Your status keeps a job."""
from job_radar import config, steps
from test_steps import RolledBack, add_job, conn  # noqa: F401  (conn is the warehouse fixture)


def test_transform_removes_a_job_posted_long_ago_though_first_seen_today(conn):
    conn.execute("INSERT INTO raw.job_posting (run_date, source, title, company, location, searched_for, job_url, payload,"
                 " last_seen) VALUES (current_date, 'test', 'Data Engineer', 'Zzoldpostco', 'Cairo', 'Egypt',"
                 " 'https://test.example/old', '{}', current_date)")  # transform needs one posting in the window
    jobs = {name: add_job(conn, name, age=3) for name in ("old", "recent", "undated", "old_but_yours")}
    for name, days in (("old", config.KEEP_DAYS + 20), ("recent", 2), ("old_but_yours", 400)):
        conn.execute("UPDATE core.job SET posted_at = now() - make_interval(days => %s) WHERE job_id = %s", (days, jobs[name]))
    conn.execute("INSERT INTO core.application (job_id, status) VALUES (%s, 'saved')", (jobs["old_but_yours"],))
    steps.transform(RolledBack(conn))
    kept = {name for name, job_id in jobs.items() if conn.execute("SELECT 1 FROM core.job WHERE job_id = %s", (job_id,)).fetchone()}
    assert kept == {"recent", "undated", "old_but_yours"}
