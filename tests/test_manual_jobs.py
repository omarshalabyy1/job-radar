"""Jobs Claude scraped by hand for one site are stored like any source's postings, once each."""
from job_radar import manual_jobs
from test_steps import conn  # noqa: F401  (conn is the warehouse fixture)

JOBS = [
    {"title": "Data Engineer", "company": "Acme", "location": "Cairo, Egypt", "job_url": "https://acme.test/jobs/1", "date_posted": "2026-11-09",
     "description": "Build pipelines.", "searched_for": "Egypt"},
    {"title": "AI Engineer", "job_url": "https://acme.test/jobs/2"},
    {"title": "No link", "company": "Acme"},  # without a link it is no posting
]


def count(conn):
    return conn.execute("SELECT count(*) FROM raw.job_posting WHERE source = 'manual:zztest'").fetchone()[0]


def test_postings_are_stored_under_manual_site_once_each(conn):
    manual_jobs.add(conn, "zztest", JOBS)
    assert count(conn) == 2
    manual_jobs.add(conn, "zztest", JOBS)  # a second scrape of the same page adds nothing
    assert count(conn) == 2
    assert conn.execute("SELECT title, company, location, searched_for, payload->>'scraped_by' FROM raw.job_posting"
                        " WHERE source = 'manual:zztest' AND job_url = 'https://acme.test/jobs/1'").fetchone() == (
        "Data Engineer", "Acme", "Cairo, Egypt", "Egypt", "claude")
