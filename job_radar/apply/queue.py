"""Which graded jobs the apply runner may touch, and the record of what it did (core.apply_attempt). Every guardrail of
docs/superpowers/plans/2026-11-08-auto-apply.md that decides *which* job lives here; the runner and the adapters come later.
`pick` is read-only: it returns the jobs to try, best first, inside the score floor, freshness, eligibility, allow-list, daily caps,
one-per-company, place (Egypt and Remote only) and kill-switch rules. A job with any attempt row is never picked again."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from psycopg.types.json import Jsonb

from ..config import OUTPUT, SETTINGS

STOP_FILE = OUTPUT / "apply.stop"
COUNTED = ("submitted", "unconfirmed")  # outcomes that count against the caps and the company rule
BOARDS = (("greenhouse", "greenhouse.io"), ("lever", "lever.co"), ("workable", "workable.com"), ("ashby", "ashbyhq.com"),
          ("smartrecruiters", "smartrecruiters.com"), ("recruitee", "recruitee.com"), ("linkedin", "linkedin.com"),
          ("indeed", "indeed.com"))


@dataclass(frozen=True)
class Job:
    job_id: int
    company: str
    board: str
    url: str
    score: int


def board_of(url: str) -> str:
    host = urlsplit(url or "").hostname or ""
    return next((name for name, domain in BOARDS if host == domain or host.endswith("." + domain)), "other")


def pick(conn, now: datetime, cfg: dict | None = None, stop_file: Path | None = None) -> list[Job]:
    cfg = cfg or SETTINGS["apply"]
    if not cfg["enabled"] or (stop_file or STOP_FILE).exists():
        return []
    rows = conn.execute(
        "SELECT j.job_id, j.company, coalesce(j.apply_url, j.job_url), g.score FROM core.job j JOIN core.claude_grade g USING (job_id)"
        " WHERE g.score >= %s AND j.place = ANY(%s) AND coalesce(j.restriction, '') = '' AND coalesce(g.restriction, '') = ''"
        " AND coalesce(j.years_required, 0) <= %s AND coalesce(j.posted_at, j.first_seen::timestamptz) >= %s"
        " AND NOT EXISTS (SELECT 1 FROM core.application a WHERE a.job_id = j.job_id"
        "                 AND a.status IN ('applied', 'interview', 'offer', 'rejected', 'ignored'))"
        " AND NOT EXISTS (SELECT 1 FROM core.apply_attempt t WHERE t.job_id = j.job_id)"
        " ORDER BY g.score DESC, coalesce(j.posted_at, j.first_seen::timestamptz) DESC, j.job_id",
        (cfg["score_floor"], cfg["places"], cfg["max_years"], now - timedelta(hours=cfg["fresh_hours"]))).fetchall()
    done_today = dict(conn.execute(
        "SELECT board, count(*) FROM core.apply_attempt WHERE outcome = ANY(%s) AND (started_at AT TIME ZONE 'UTC')::date = %s GROUP BY board",
        (list(COUNTED), now.date())).fetchall())
    recent = {c for (c,) in conn.execute(
        "SELECT lower(j.company) FROM core.apply_attempt t JOIN core.job j USING (job_id) WHERE t.outcome = ANY(%s) AND t.started_at >= %s",
        (list(COUNTED), now - timedelta(days=cfg["company_days"])))}
    total = sum(done_today.values())
    picked: list[Job] = []
    for job_id, company, url, score in rows:
        board = board_of(url)
        cap = cfg["board_caps"].get(board, cfg["per_board"])
        if (board not in cfg["boards"] or total >= cfg["daily_total"] or done_today.get(board, 0) >= cap
                or company.lower() in recent):
            continue
        picked.append(Job(job_id, company, board, url, score))
        done_today[board] = done_today.get(board, 0) + 1
        total += 1
        recent.add(company.lower())  # one company once per batch
    return picked


def record_attempt(conn, job_id: int, board: str, outcome: str, answers: dict | None = None, screenshot: str | None = None,
                   confirmation: str | None = None, when: datetime | None = None) -> int:
    return conn.execute(
        "INSERT INTO core.apply_attempt (job_id, board, outcome, answers, screenshot, confirmation, started_at, finished_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, coalesce(%s, now()), coalesce(%s, now())) RETURNING id",
        (job_id, board, outcome, Jsonb(answers) if answers is not None else None, screenshot, confirmation, when, when)).fetchone()[0]
