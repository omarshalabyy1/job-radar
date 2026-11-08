"""The weekly ops review: one report from the local warehouse on how the last seven days went.

    python -m job_radar.review weekly      # writes output/reviews/weekly-<day>.md and prints its path

Read-only: every figure is one SELECT on one table (core.step_run, core.application, core.claude_grade,
core.application_email, and core.apply_question once the Applier exists). It writes nothing to the warehouse, sends nothing
to anyone and reads no secret; the only file it writes is the report, under OUTPUT / "reviews". The window is
`now - 7 days < time <= now` for every figure, so a row exactly 7 days old is out and one a minute younger is in.
Writing the report twice for the same day gives the same path and, for the same warehouse, the same text."""

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from . import db
from .config import OUTPUT

WINDOW = timedelta(days=7)
# a reply is an answer from an employer; 'applied' is a board confirming Omar's own application and 'other' is unclassified
REPLY_KINDS = ("viewed", "rejected", "interview", "offer", "hired")
READ = ("core.step_run", "core.application", "core.claude_grade", "core.application_email")


def _steps(conn, now: datetime) -> list[tuple[str, int, int, int, Decimal]]:
    """(step, runs, failed, warned, cost) per step. A 'running' row counts as a run only; a NULL cost counts as 0."""
    return [(step, runs, failed, warned, cost) for step, runs, failed, warned, cost in conn.execute(
        "SELECT step, count(*), count(*) FILTER (WHERE status = 'failed'), count(*) FILTER (WHERE status = 'warn'),"
        " coalesce(sum(cost_usd), 0) FROM core.step_run"
        " WHERE started_at > %(now)s - interval '7 days' AND started_at <= %(now)s GROUP BY step ORDER BY step",
        {"now": now}).fetchall()]


def _graded(conn, now: datetime) -> int:
    return conn.execute(
        "SELECT count(*) FROM core.claude_grade WHERE score >= 8"
        " AND graded_at > %(now)s - interval '7 days' AND graded_at <= %(now)s", {"now": now}).fetchone()[0]


def _moved(conn, now: datetime) -> dict[str, int]:
    """Applications whose status was set this week, by status."""
    return dict(conn.execute(
        "SELECT status, count(*) FROM core.application"
        " WHERE updated_at > %(now)s - interval '7 days' AND updated_at <= %(now)s GROUP BY status ORDER BY status",
        {"now": now}).fetchall())


def _replies(conn, now: datetime) -> dict[str, int]:
    """Employer replies read from the mailboxes this week, by kind."""
    return dict(conn.execute(
        "SELECT kind, count(*) FROM core.application_email WHERE kind = ANY(%(kinds)s)"
        " AND received_at > %(now)s - interval '7 days' AND received_at <= %(now)s GROUP BY kind ORDER BY kind",
        {"now": now, "kinds": list(REPLY_KINDS)}).fetchall())


def _parked(conn) -> int | None:
    """Questions the Applier parked for Omar that he has not answered; None while the Applier's table does not exist."""
    if conn.execute("SELECT to_regclass('core.apply_question') IS NULL").fetchone()[0]:
        return None
    return conn.execute("SELECT count(*) FROM core.apply_question WHERE answer IS NULL").fetchone()[0]


def _counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{name} {n}" for name, n in sorted(counts.items())) or "none"


def render(conn, now: datetime) -> str:
    """The report as markdown text."""
    steps = _steps(conn, now)
    start = now - WINDOW
    lines = ["# Weekly ops review", "",
             f"Window: {start:%Y-%m-%d %H:%M} to {now:%Y-%m-%d %H:%M} UTC (7 days)", ""]
    if steps:
        lines += ["| step | runs | failed | warned | cost_usd |", "|---|---|---|---|---|"]
        lines += [f"| {step} | {runs} | {failed} | {warned} | {cost:.2f} |" for step, runs, failed, warned, cost in steps]
        lines += [f"| total | {sum(s[1] for s in steps)} | {sum(s[2] for s in steps)} | {sum(s[3] for s in steps)} |"
                  f" {sum(s[4] for s in steps):.2f} |"]
    else:
        lines += ["No runs in the last 7 days."]
    worst = min(steps, key=lambda s: (-s[2], -s[3], s[0]), default=None)  # most failed, then most warned, then by name
    priciest = min(steps, key=lambda s: (-s[4], s[0]), default=None)
    lines += ["",
              f"Least reliable step: {worst[0]} ({worst[2]} failed, {worst[3]} warned)"
              if worst and (worst[2] or worst[3]) else "Least reliable step: none",
              f"Most expensive step: {priciest[0]} (${priciest[4]:.2f})" if priciest and priciest[4] else "Most expensive step: none",
              f"Jobs graded 8 or higher this week: {_graded(conn, now)}",
              f"Applications moved this week: {_counts(_moved(conn, now))}",
              f"Replies this week: {_counts(_replies(conn, now))}"]
    parked = _parked(conn)
    lines += [f"Parked questions unanswered: {'Applier not built' if parked is None else parked}",
              "", "Read-only: " + ", ".join(READ)]
    return "\n".join(lines) + "\n"


def write_weekly(conn, now: datetime, out_dir: Path = OUTPUT / "reviews") -> Path:
    """Write weekly-<YYYY-MM-DD>.md (the day of `now`, UTC) and return its path; the same day gives the same path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"weekly-{now.astimezone(timezone.utc):%Y-%m-%d}.md"
    path.write_text(render(conn, now), encoding="utf-8")
    return path


def main() -> int:
    if sys.argv[1:] != ["weekly"]:
        print("usage: python -m job_radar.review weekly")
        return 2
    conn = db.connect()
    try:
        print(write_weekly(conn, datetime.now(timezone.utc), OUTPUT / "reviews"))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
