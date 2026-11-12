"""Neon's jobs.job refuses a salary_period outside yearly, annual, monthly, weekly, daily, hourly; boards say "year", "day", "hour". One such
job (Distru, Lever) failed the whole publish with a CheckViolation (2026-11-10)."""

from job_radar import publish
from tests.test_steps import conn  # noqa: F401  (the rolled-back warehouse connection)


def test_a_boards_period_word_becomes_one_neon_accepts_and_an_unknown_one_becomes_null(conn):
    expr = publish.WIDE_SQL["salary_period"]
    got = dict(conn.execute(f"SELECT p, {expr} FROM (VALUES ('year'), ('day'), ('hour'), ('month'), ('week'), ('yearly'), ('annual'),"
                            " ('fortnight'), (NULL)) v(salary_period) CROSS JOIN LATERAL (SELECT salary_period AS p) x").fetchall())
    assert got == {"year": "yearly", "day": "daily", "hour": "hourly", "month": "monthly", "week": "weekly", "yearly": "yearly",
                   "annual": "annual", "fortnight": None, None: None}
