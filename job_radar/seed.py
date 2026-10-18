"""seed: fill an empty warehouse from Neon, for the GitHub Actions fallback (.github/workflows/daily-backup.yml), which
collects on a fresh runner on a day the laptop did not.

Every jobs.job row goes into core.job with Neon's own job_key, copied as it is (never computed again: steps.job_key
may change), so a job the run finds again is matched to it and keeps its first_seen, and publish gives it the same id
as the laptop does (its id is made from job_key). Each row is marked described, judged and emailed, so describe,
eligibility and the emails leave it alone; its in_scope, last_seen, work_mode, role_family, track, seniority and pay
are Neon's, so publish gives back what it read. core.job numbers the rows
itself: publish does not use those numbers. A second run adds nothing (ON CONFLICT DO NOTHING).
Needs jobs.job.job_key on Neon: without it this stops with an error and nothing is published.
Run it once, right after `python -m job_radar schema`, never on the laptop. NEON_DATABASE_URL is never printed."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import psycopg

from .applications import accounts
from .config import ALERT_RANK, ROLE_LABEL
from .db import connect

COLUMNS = ("job_key", "title", "company", "location", "place", "role", "source", "job_url", "apply_url",
           "company_size", "years_required", "restriction", "posted_at", "first_seen", "description")
# What publish writes from core.job as well, so a seeded row does not publish its defaults over what Neon holds (in_scope true,
# no last_seen, no work_mode, role_family, track or seniority): the laptop's values, taken back from Neon. Only those Neon has.
KEPT = ("work_mode", "countries", "regions", "visa_relocation", "nationals_only", "arabic_required", "salary_min", "salary_max",
        "salary_currency", "salary_period", "salary_min_usd", "salary_max_usd", "salary_min_eur", "salary_max_eur", "salary_plausible",
        "in_scope", "last_seen", "role_family", "track", "seniority")


def main() -> None:
    try:
        neon = psycopg.connect(os.environ["NEON_DATABASE_URL"], connect_timeout=30)
    except psycopg.Error:
        raise SystemExit("seed: cannot connect to Neon; check NEON_DATABASE_URL") from None  # the error can quote the url
    with neon:
        have = {c for (c,) in neon.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = 'jobs' AND table_name = 'job'")}
        kept = tuple(c for c in KEPT if c in have)
        jobs = neon.execute(f"SELECT {', '.join(COLUMNS + kept)} FROM jobs.job ORDER BY job_key").fetchall()
        mails = neon.execute("SELECT e.message_id, e.received_at, e.sender, e.subject, e.company, e.title, e.kind, j.job_key"
                             " FROM tracker.application_email e LEFT JOIN jobs.job j USING (job_id)").fetchall()
    if any(j[0] is None for j in jobs):
        raise SystemExit("seed: jobs.job.job_key is not filled on Neon yet (the laptop's next publish fills it); nothing seeded")
    rank = {label: r for r, label in ROLE_LABEL.items()}
    rows = [{**dict(zip(COLUMNS + kept, j)), "role_rank": rank.get(j[5], ALERT_RANK)} for j in jobs]
    with connect() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO core.job (job_key, title, company, location, place, source, job_url, role_rank, role,"
            " target_company, apply_url, company_size, years_required, restriction, posted_at, first_seen, description,"
            f" described_at, eligibility_at, emailed_at{''.join(', ' + c for c in kept)}) VALUES (%(job_key)s, %(title)s, %(company)s,"
            " %(location)s, %(place)s, %(source)s, %(job_url)s, %(role_rank)s, %(role)s, false, %(apply_url)s, %(company_size)s,"
            " %(years_required)s, %(restriction)s, %(posted_at)s, %(first_seen)s, %(description)s, now(), now(), now()"
            f"{''.join(f', %({c})s' for c in kept)}) ON CONFLICT DO NOTHING", rows)
        seeded = conn.execute("SELECT count(*) FROM core.job").fetchone()[0]
        # The application emails read already (track_applications reads only the emails not stored), their job found by job_key.
        # Neon does not hold the mailbox, so each Gmail account gets one placeholder (kind 'other', which publish_applications
        # leaves out): an account with something stored is read 3 days back, not 400.
        cur.executemany(
            "INSERT INTO core.application_email (message_id, received_at, sender, subject, company, title, kind, job_id)"
            " SELECT %(m)s, %(r)s, %(s)s, %(u)s, %(c)s, %(t)s, %(k)s, (SELECT job_id FROM core.job WHERE job_key = %(j)s)"
            " ON CONFLICT DO NOTHING",
            [dict(zip("mrsuctkj", mail)) for mail in mails]
            + [dict(m=f"seed/{address}", r=datetime.now(timezone.utc), s=address, u="seed", c=None, t=None, k="other", j=None)
               for address in accounts()])
        cur.executemany("UPDATE core.application_email SET mailbox = %s WHERE message_id = %s",
                        [(address, f"seed/{address}") for address in accounts()])
    print(f"seed: {len(jobs)} Neon jobs read, {seeded} in core.job, {len(mails)} application emails")


if __name__ == "__main__":
    main()
