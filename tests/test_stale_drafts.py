"""A job Claude saved by itself (tailor's 'draft ready', the skill's 'tailored') is no action of yours: it ages out after
KEEP_DAYS like any job, while a save or any other status you set keeps the job."""
from job_radar import config, steps
from test_steps import RolledBack, add_job, conn  # noqa: F401  (conn is the warehouse fixture)


def test_transform_ages_out_a_job_only_claude_saved_and_keeps_one_you_acted_on(conn):
    old = config.KEEP_DAYS + 5
    conn.execute("INSERT INTO raw.job_posting (run_date, source, title, company, location, searched_for, job_url, payload,"
                 " last_seen) VALUES (current_date, 'test', 'Data Engineer', 'Zzstaledraftco', 'Cairo', 'Egypt',"
                 " 'https://test.example/stale', '{}', current_date)")  # transform needs one posting in the window
    jobs = {name: add_job(conn, name, age=old) for name in ("draft", "tailored", "your_save", "applied_with_draft", "no_status")}
    for name, status, note in (("draft", "saved", "draft ready 2026-10-30"), ("tailored", "saved", "tailored 2026-10-10"),
                               ("your_save", "saved", None), ("applied_with_draft", "applied", "draft ready 2026-10-30")):
        conn.execute("INSERT INTO core.application (job_id, status, note) VALUES (%s, %s, %s)", (jobs[name], status, note))
    steps.transform(RolledBack(conn))
    kept = {name for name, job_id in jobs.items()
            if conn.execute("SELECT 1 FROM core.job WHERE job_id = %s", (job_id,)).fetchone()}
    assert kept == {"your_save", "applied_with_draft"}
    gone = [job_id for name, job_id in jobs.items() if name in ("draft", "tailored")]
    assert conn.execute("SELECT count(*) FROM core.job_archive WHERE job_id = ANY(%s)", (gone,)).fetchone() == (2,)  # copied before deleting
    assert conn.execute("SELECT count(*) FROM core.application WHERE job_id = ANY(%s)", (gone,)).fetchone() == (0,)
