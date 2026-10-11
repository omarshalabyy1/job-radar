"""grade_claude: Claude (settings.yaml claude.model) scores each job that has a description, in a place of
ai_places, against your CVs in core.cv, 0-10 (again after you upload a CV), names the CV to send, gives one line why and (features.eligibility)
says what keeps someone living in Cairo from taking it (restriction), into core.claude_grade. The job_radar DAG
runs it after embed; the emails, the tracker and the tailor step read the grade, and a job scored 3 or lower, or
with a restriction (features.eligibility), is hidden (config.hidden_reason, features.auto_hide). It learns from your last 30
clicks. With features.grade false, no ANTHROPIC_API_KEY or no CV it prints why and stops green. When jobs were selected
and none was graded (every call failed, or the credit ran out) it prints the summary and fails, so the task is red
and the run log says failed (a refused call costs nothing; the next run tries again)."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel

from . import claude
from .config import AI_PLACES, FEATURES, KEEP_DAYS, SETTINGS
from .sources import time_left

GRADE_SECONDS = 540  # the DAG gives the task 600 s; the jobs left are graded next run
MAX_TOKENS = 512  # the largest grade measured was 128 output tokens (30 jobs, 2026-10-11): 4x room
PICKED = ("saved", "applied", "interview", "offer")  # the statuses that say you wanted the job


class Grade(BaseModel):
    score: int        # 0-10
    cv: str           # the label of the CV to send
    reason: str
    restriction: str  # empty when someone living in Cairo, Egypt can legally take the job


def jobs_to_grade(conn, limit: int) -> list[tuple]:
    """The jobs still alive, in ai_places, with a description (a title alone is a weak signal, so those stay
    ungraded), that the eligibility step did not bar (with features.eligibility), with no grade yet or one made before your newest
    CV was uploaded (graded again: graded_at >= uploaded_at counts as fresh) and that you did not ignore or
    reject: most skills first, then freshest."""
    return conn.execute(
        "SELECT j.job_id, j.title, j.company, j.location, j.description FROM core.job j"
        " WHERE j.first_seen >= current_date - %s AND j.place = ANY(%s) AND j.description IS NOT NULL"
        " AND (j.restriction IS NULL OR NOT %s)"
        " AND NOT EXISTS (SELECT 1 FROM core.claude_grade g WHERE g.job_id = j.job_id"
        " AND g.graded_at >= (SELECT max(uploaded_at) FROM core.cv))"
        " AND NOT EXISTS (SELECT 1 FROM core.application a WHERE a.job_id = j.job_id AND a.status IN ('ignored', 'rejected'))"
        " ORDER BY (SELECT count(*) FROM core.job_skill k WHERE k.job_id = j.job_id) DESC,"
        " j.posted_at DESC NULLS LAST, j.job_id LIMIT %s", (KEEP_DAYS, AI_PLACES, FEATURES["eligibility"], limit)).fetchall()


def recent_clicks(conn) -> list[tuple]:
    """Your last 30 clicks, as (line, picked): examples of what you pick and what you pass on. The 'saved' row the
    tailor step writes with its "draft ready" note is not a click of yours."""
    return conn.execute(
        "SELECT j.title || ' at ' || j.company || ' -> ' || a.status, a.status = ANY(%s) FROM core.application a"
        " JOIN core.job j USING (job_id) WHERE a.status <> 'new' AND NOT (a.status = 'saved' AND coalesce(a.note, '') LIKE 'draft ready%%')"
        " ORDER BY a.updated_at DESC LIMIT 30",
        (list(PICKED),)).fetchall()


def system_blocks(cvs: list[tuple], clicks: list[tuple], eligibility: bool) -> list[dict]:
    """The CVs and the clicks are the same for every job: cached, so each call after the first reads them at a tenth of the price."""
    picked = "\n".join(line for line, yes in clicks if yes)
    passed = "\n".join(line for line, yes in clicks if not yes)
    return [{"type": "text", "cache_control": {"type": "ephemeral"},
             "text": "You grade job postings for one candidate against their CVs."
                     + (" The candidate lives in Cairo, Egypt, has no work permit anywhere else and wants a remote job abroad, "
                        "or a job in Cairo or Giza; a job they cannot legally take from Egypt (a citizenship, a "
                        "security clearance, a work permit or residence in another country, nationals only) "
                        "scores 0-3." if eligibility else "")
                     + " score: 0-10, how well they fit and could get it (10 = apply today). cv: the label of the "
                       "CV they should send, exactly as given. reason: one short line naming the deciding match or "
                       "gap. restriction: "
                     + ("empty when someone living in Cairo, Egypt can legally take the job, else a short phrase "
                        "saying what bars them." if eligibility else "always empty.")
                     + "\n\n" + "\n\n".join(f"<cv label=\"{label}\">\n{text}\n</cv>" for label, text in cvs)
                     + f"\n\nJobs they picked (saved or applied to) and jobs they passed on (ignored or rejected), "
                       f"newest first; grade like them:\n<picked>\n{picked}\n</picked>\n<passed>\n{passed}\n</passed>"}]


def save_grades(conn, rows: list[tuple]) -> None:
    """One row per job (job_id is the key): a job graded again overwrites its grade and stamps graded_at."""
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO core.claude_grade (job_id, score, cv, reason, restriction, model)"
                        " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (job_id) DO UPDATE SET score = EXCLUDED.score,"
                        " cv = EXCLUDED.cv, reason = EXCLUDED.reason, restriction = EXCLUDED.restriction,"
                        " model = EXCLUDED.model, graded_at = now()", rows)


def grade_claude(conn) -> None:
    cfg = SETTINGS["claude"]
    if not FEATURES["grade"]:
        print("grade_claude: off in settings.yaml (features.grade)")
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("grade_claude: no ANTHROPIC_API_KEY in .env, skipped")
        return
    cvs = conn.execute("SELECT label, text FROM core.cv ORDER BY label").fetchall()
    if not cvs:
        print("grade_claude: no CV uploaded in the tracker, skipped")
        return
    jobs = jobs_to_grade(conn, cfg["max_jobs"])
    eligibility = FEATURES["eligibility"]
    system = system_blocks(cvs, recent_clicks(conn), eligibility)
    import anthropic  # for its errors; the SDK loads only in a run that calls Claude
    client = claude.client()  # a stuck call gives up well inside the DAG's 600 s
    out_of_credit = False

    def grade(job: tuple) -> tuple | None:
        nonlocal out_of_credit
        job_id, title, company, location, description = job
        if out_of_credit or time_left(GRADE_SECONDS) < 30:
            return None
        try:
            r = client.messages.parse(
                model=cfg["model"], max_tokens=MAX_TOKENS, system=system, output_format=Grade,
                output_config={"effort": "low"},
                messages=[{"role": "user", "content": f"{title} at {company} ({location})\n\n{description[:6000]}"}])
        except anthropic.BadRequestError as e:
            if claude.out_of_credit(e):  # the balance ran out: every call would be refused
                out_of_credit = True
            else:  # one job refused: a warning, never the loss of the grades this run already paid for
                print(f"WARNING grade_claude: job {job_id} refused: {e!r}"[:300])
            return None
        except anthropic.APIError as e:  # one job down is a warning: the emails still go out
            print(f"WARNING grade_claude: job {job_id}: {e!r}"[:300])
            return None
        g = r.parsed_output
        if g is None:
            return None
        return (job_id, max(0, min(10, g.score)), g.cv, g.reason, (g.restriction.strip() or None) if eligibility else None,
                cfg["model"])

    # the first job alone writes the cache; only then do the other calls start, so they all read it
    first = [grade(jobs[0])] if jobs else []
    with ThreadPoolExecutor(4) as pool:
        done = [row for row in [*first, *pool.map(grade, jobs[1:])] if row]
    save_grades(conn, done)
    conn.commit()
    print(f"grade_claude: {len(done)} of {len(jobs)} jobs graded with {cfg['model']}"
          + (" - WARNING the API credit ran out: no more grades until you buy more" if out_of_credit else ""))
    if jobs and not done:
        raise RuntimeError(f"grade_claude: 0 of {len(jobs)} jobs graded - check the API key, credit and network")
