"""seed: fill an empty warehouse from Neon, for the GitHub Actions fallback (.github/workflows/daily-backup.yml), which
collects on a fresh runner on a day the laptop did not.

Every jobs.job row goes into core.job with Neon's own job_key, copied as it is (never computed again: steps.job_key
may change), so a job the run finds again is matched to it and keeps its first_seen, and publish gives it the same id
as the laptop does (its id is made from job_key). Each row is marked described, judged and emailed, so describe,
eligibility and the emails leave it alone and transform does not remove it as out of scope. core.job numbers the rows
itself: publish does not use those numbers. A second run adds nothing (ON CONFLICT DO NOTHING).
Needs jobs.job.job_key on Neon: without it this stops with an error and nothing is published.
Run it once, right after `python -m job_radar schema`, never on the laptop. NEON_DATABASE_URL is never printed."""

from __future__ import annotations

import os

import psycopg

from .config import ALERT_RANK, ROLE_LABEL
from .db import connect

COLUMNS = ("job_key", "title", "company", "location", "place", "role", "source", "job_url", "apply_url",
           "company_size", "years_required", "restriction", "posted_at", "first_seen", "description")


def main() -> None:
    try:
        neon = psycopg.connect(os.environ["NEON_DATABASE_URL"], connect_timeout=30)
    except psycopg.Error:
        raise SystemExit("seed: cannot connect to Neon; check NEON_DATABASE_URL") from None  # the error can quote the url
    with neon:
        jobs = neon.execute(f"SELECT {', '.join(COLUMNS)} FROM jobs.job ORDER BY job_key").fetchall()
    if any(j[0] is None for j in jobs):
        raise SystemExit("seed: jobs.job.job_key is not filled on Neon yet (the laptop's next publish fills it); nothing seeded")
    rank = {label: r for r, label in ROLE_LABEL.items()}
    rows = [{**dict(zip(COLUMNS, j)), "role_rank": rank.get(j[5], ALERT_RANK)} for j in jobs]
    with connect() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO core.job (job_key, title, company, location, place, source, job_url, role_rank, role,"
            " target_company, apply_url, company_size, years_required, restriction, posted_at, first_seen, description,"
            " described_at, eligibility_at, emailed_at) VALUES (%(job_key)s, %(title)s, %(company)s, %(location)s,"
            " %(place)s, %(source)s, %(job_url)s, %(role_rank)s, %(role)s, false, %(apply_url)s, %(company_size)s,"
            " %(years_required)s, %(restriction)s, %(posted_at)s, %(first_seen)s, %(description)s, now(), now(), now())"
            " ON CONFLICT DO NOTHING", rows)
        seeded = conn.execute("SELECT count(*) FROM core.job").fetchone()[0]
    print(f"seed: {len(jobs)} Neon jobs read, {seeded} in core.job")


if __name__ == "__main__":
    main()
