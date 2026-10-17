"""Proof that the website's collection is idempotent: on the warehouse, in one transaction that is rolled back at the end
(nothing stays), read a few company boards, then transform and match_skills, twice over, and print each table's row
count and checksum after each round. Identical lines = re-running the same day (or after a crash) gives the same rows.

    python scripts/idempotency_check.py [boards]      # default 2: the two oldest-read boards of core.board

The schema is applied first inside the transaction, so it checks the working tree's sql/schema.sql too. The ECB rates
are read live in both rounds (stored per date, so the same)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_radar import db, steps  # noqa: E402

# what a re-run must leave unchanged: every column but the surrogate keys and load times
CHECKS = {
    "raw.job_posting": "SELECT source, job_url, title, company, location, date_posted, description, payload::text, last_seen"
                       " FROM raw.job_posting ORDER BY source, job_url",
    "core.job": "SELECT job_key, title, company, location, place, source, role, in_scope, last_seen, work_mode, countries,"
                " regions, visa_relocation, nationals_only, arabic_required, salary_min, salary_max, salary_currency,"
                " salary_min_usd, salary_max_usd, role_family, track, seniority FROM core.job ORDER BY job_key",
    "core.job_skill": "SELECT j.job_key, k.skill FROM core.job_skill k JOIN core.job j USING (job_id) ORDER BY 1, 2",
    "core.skill_day": "SELECT day, skill, jobs, total_jobs FROM core.skill_day ORDER BY day, skill",
    "core.board": "SELECT ats, token, status, failures, jobs FROM core.board ORDER BY ats, token",
}


class RolledBack:
    """The connection with commit turned into nothing: every step's work stays in the one transaction."""

    def __init__(self, conn):
        self.conn = conn

    def __getattr__(self, name):
        return getattr(self.conn, name)

    def commit(self):
        pass


def checksums(conn) -> dict:
    return {name: conn.execute(f"SELECT count(*), md5(coalesce(string_agg(t::text, '|'), '')) FROM ({sql}) t").fetchone()
            for name, sql in CHECKS.items()}


def main(boards: int) -> None:
    conn = db.connect()
    try:
        conn.execute("SET lock_timeout = '10s'")
        conn.execute((steps.ROOT / "sql" / "schema.sql").read_text(encoding="utf-8"))
        run = RolledBack(conn)
        steps.FEATURES["market"] = True
        steps.MARKET_BOARDS = boards
        rounds = []
        for n in (1, 2):
            if n == 2:  # read the same boards again, as a second run the same day after a crash would
                conn.execute("UPDATE core.board SET checked_at = NULL WHERE checked_at >= now() - interval '1 hour'")
            steps.extract_market(run)
            steps.transform(run)
            steps.match_skills(run)
            rounds.append(checksums(conn))
        for name in CHECKS:
            first, second = rounds[0][name], rounds[1][name]
            print(f"{name:16} round 1: {first[0]:6} rows {first[1]}   round 2: {second[0]:6} rows {second[1]}"
                  f"   {'same' if first == second else 'DIFFERENT'}")
        if rounds[0] != rounds[1]:
            raise SystemExit("not idempotent: a second round changed rows")
    finally:
        conn.rollback()
        conn.close()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 2)
