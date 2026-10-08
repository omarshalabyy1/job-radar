"""grade_claude: Claude (settings.yaml claude.model) scores each new job of the window 0-10 against your
CVs in core.cv, names the CV to send and gives one line why, into core.claude_grade. A side step:
nothing else reads that table, and with claude.enabled false, no ANTHROPIC_API_KEY, no CV or no credit
left it prints why and stops green (a refused call costs nothing; the next run tries again)."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import anthropic
from pydantic import BaseModel

from .config import SETTINGS
from .sources import time_left

GRADE_SECONDS = 540  # the DAG gives the task 600 s; the jobs left are graded next run


class Grade(BaseModel):
    score: int  # 0-10
    cv: str     # the label of the CV to send
    reason: str


def grade_claude(conn) -> None:
    cfg = SETTINGS.get("claude") or {}
    if not cfg.get("enabled"):
        print("grade_claude: claude.enabled is false in settings.yaml, skipped")
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("grade_claude: no ANTHROPIC_API_KEY in .env, skipped")
        return
    cvs = conn.execute("SELECT label, text FROM core.cv ORDER BY label").fetchall()
    if not cvs:
        print("grade_claude: no CV uploaded in the tracker, skipped")
        return
    jobs = conn.execute(
        "SELECT j.job_id, j.title, j.company, j.location, j.description FROM core.job j"
        " WHERE j.first_seen >= current_date - 1 AND NOT EXISTS"
        " (SELECT 1 FROM core.claude_grade g WHERE g.job_id = j.job_id)"
        " ORDER BY j.role_rank, j.job_id LIMIT %s", (cfg["max_jobs"],)).fetchall()
    # the CVs are the same for every job: cached, so each call after the first reads them at a tenth of the price
    system = [{"type": "text", "cache_control": {"type": "ephemeral"},
               "text": "You grade job postings for one candidate against their CVs. score: 0-10, how well they fit "
                       "and could get it (10 = apply today). cv: the label of the CV they should send, exactly as "
                       "given. reason: one short line naming the deciding match or gap.\n\n"
                       + "\n\n".join(f"<cv label=\"{label}\">\n{text}\n</cv>" for label, text in cvs)}]
    client = anthropic.Anthropic()
    out_of_credit = False

    def grade(job: tuple) -> tuple | None:
        nonlocal out_of_credit
        job_id, title, company, location, description = job
        if out_of_credit or time_left(GRADE_SECONDS) < 30:
            return None
        try:
            r = client.messages.parse(
                model=cfg["model"], max_tokens=2000, system=system, output_format=Grade,
                output_config={"effort": "low"},
                messages=[{"role": "user", "content": f"{title} at {company} ({location})\n\n"
                                                      f"{(description or 'No description.')[:6000]}"}])
        except anthropic.BadRequestError as e:
            if "credit" in str(e).lower():  # the balance ran out: every call would be refused
                out_of_credit = True
                return None
            raise
        g = r.parsed_output
        return None if g is None else (job_id, max(0, min(10, g.score)), g.cv, g.reason, cfg["model"])

    with ThreadPoolExecutor(4) as pool:
        graded = [g for g in pool.map(grade, jobs) if g]
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO core.claude_grade (job_id, score, cv, reason, model) VALUES (%s, %s, %s, %s, %s)"
                        " ON CONFLICT (job_id) DO NOTHING", graded)
    conn.commit()
    print(f"grade_claude: {len(graded)} of {len(jobs)} new jobs graded with {cfg['model']}"
          + (" - WARNING the API credit ran out: no more grades until you buy more" if out_of_credit else ""))
