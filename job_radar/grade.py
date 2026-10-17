"""grade_claude: Claude (settings.yaml claude.model) scores the jobs with a description, in any place, closest to your
CV first (core.job.cv_similarity, embed's free score), with no daily cap, against your CVs in core.cv, 0-10
(again after you upload a CV), names the CV to send, gives one line why (naming a years-of-experience mismatch) and
(features.eligibility) says what keeps someone living in Cairo from taking it (restriction), into core.claude_grade. A
job whose description gave no years asked (steps.years_required) gets Claude's reading in core.job.years_required. The job_radar DAG
runs it after embed; the emails, the tracker and the tailor step read the grade, and a job scored 3 or lower, or
with a restriction (features.eligibility), is hidden (config.hidden_reason, features.auto_hide). It learns from your last 30
clicks and from the last 30 jobs your mailbox shows answering you (an interview or offer, or a rejection). A job graded
8+ that names a closing date gets it as an "Apply by" deadline on the Today page. The free models grade first (llm.py:
Groq, Gemini, OpenRouter), Claude only when none answers; core.claude_grade.model names who graded each job.
With features.grade false, no model key at all or no CV it prints why and stops green. When jobs were selected
and none was graded (every call failed, or the credit ran out) it prints the summary and fails, so the task is red
and the run log says failed (a refused call costs nothing; the next run tries again)."""

from __future__ import annotations

import os
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel

from . import claude, llm
from .config import FEATURES, WORK_DAYS, SETTINGS
from .digest import push
from .sources import time_left

GRADE_SECONDS = 540  # the DAG gives the task 600 s; the jobs left are graded next run
MAX_TOKENS = 512  # the largest grade measured was 128 output tokens (30 jobs, 2026-10-11): 4x room
PICKED = ("saved", "applied", "interview", "offer")  # the statuses that say you wanted the job


class Grade(BaseModel):
    score: int        # 0-10
    cv: str           # the label of the CV to send
    reason: str
    restriction: str  # empty when someone living in Cairo, Egypt can legally take the job
    years_required: int  # the years of experience the posting asks for, 0 when it names none
    apply_by: str     # the posting's closing date, YYYY-MM-DD, empty when it names none


def jobs_to_grade(conn) -> list[tuple]:
    """The jobs still alive, in any place, with a description (a title alone is a weak signal, so those stay
    ungraded), that the eligibility step did not bar (with features.eligibility), with no grade yet or one made before your newest
    CV was uploaded (graded again: graded_at >= uploaded_at counts as fresh) and that you did not ignore or
    reject: closest to your CV first (embed), then most skills, then freshest. No cap: the step's time check leaves
    the rest for the next run."""
    return conn.execute(
        "SELECT j.job_id, j.title, j.company, j.location, j.description, j.years_required FROM core.job j"
        " WHERE j.in_scope AND j.first_seen >= current_date - %s AND j.description IS NOT NULL"
        " AND (j.restriction IS NULL OR NOT %s)"
        " AND NOT EXISTS (SELECT 1 FROM core.claude_grade g WHERE g.job_id = j.job_id"
        " AND g.graded_at >= (SELECT max(uploaded_at) FROM core.cv))"
        " AND NOT EXISTS (SELECT 1 FROM core.application a WHERE a.job_id = j.job_id AND a.status IN ('ignored', 'rejected'))"
        " ORDER BY j.cv_similarity DESC NULLS LAST, (SELECT count(*) FROM core.job_skill k WHERE k.job_id = j.job_id) DESC,"
        " j.posted_at DESC NULLS LAST, j.job_id",
        (WORK_DAYS, FEATURES["eligibility"])).fetchall()


def recent_clicks(conn) -> list[tuple]:
    """Your last 30 clicks, as (line, picked): examples of what you pick and what you pass on. The 'saved' row the
    tailor step writes with its "draft ready" note is not a click of yours."""
    return conn.execute(
        "SELECT j.title || ' at ' || j.company || ' -> ' || a.status, a.status = ANY(%s) FROM core.application a"
        " JOIN core.job j USING (job_id) WHERE a.status <> 'new' AND NOT (a.status = 'saved' AND coalesce(a.note, '') LIKE 'draft ready%%')"
        " ORDER BY a.updated_at DESC LIMIT 30",
        (list(PICKED),)).fetchall()


def replies(conn) -> list[tuple]:
    """The last 30 jobs your mailbox shows answering you, as (line, answered): an interview, offer or hire is answered,
    a rejection is not. Each job's newest answer counts."""
    return conn.execute(
        "SELECT j.title || ' at ' || j.company || ' -> ' || e.kind, e.kind <> 'rejected' FROM (SELECT DISTINCT ON (job_id)"
        " job_id, kind, received_at FROM core.application_email WHERE job_id IS NOT NULL"
        " AND kind IN ('interview', 'offer', 'hired', 'rejected') ORDER BY job_id, received_at DESC) e"
        " JOIN core.job j USING (job_id) ORDER BY e.received_at DESC LIMIT 30").fetchall()


def system_blocks(cvs: list[tuple], clicks: list[tuple], eligibility: bool, answers: list[tuple] = ()) -> list[dict]:
    """The CVs, the clicks and the answers are the same for every job: cached, so each call after the first reads them
    at a tenth of the price."""
    picked = "\n".join(line for line, yes in clicks if yes)
    passed = "\n".join(line for line, yes in clicks if not yes)
    answered = "\n".join(line for line, yes in answers if yes)
    rejected = "\n".join(line for line, yes in answers if not yes)
    return [{"type": "text", "cache_control": {"type": "ephemeral"},
             "text": "You grade job postings for one candidate against their CVs."
                     + (" The candidate lives in Cairo, Egypt, has no work permit anywhere else and wants a remote job abroad, "
                        "or a job in Cairo or Giza; a job they cannot legally take from Egypt (a citizenship, a "
                        "security clearance, a work permit or residence in another country, nationals only, fluency in a "
                        "language other than Arabic or English) scores 0-3." if eligibility else "")
                     + " score: 0-10, how well they fit and could get it (10 = apply today); a posting that asks for "
                       "more years of experience than the CV shows scores lower. cv: the label of the "
                       "CV they should send, exactly as given. reason: one short line naming the deciding match or "
                       "gap; when the posting asks for more years than the CV shows, it says so first (\"asks 4+ yrs, CV "
                       "shows about 2\"). years_required: the years of experience the posting asks for (its lowest "
                       "bound, the most of several), 0 when it names none. apply_by: the closing date the posting names, "
                       "YYYY-MM-DD, empty when it names none. restriction: "
                     + ("empty when someone living in Cairo, Egypt can legally take the job, else a short phrase "
                        "saying what bars them." if eligibility else "always empty.")
                     + "\n\n" + "\n\n".join(f"<cv label=\"{label}\">\n{text}\n</cv>" for label, text in cvs)
                     + f"\n\nJobs they picked (saved or applied to) and jobs they passed on (ignored or rejected), "
                       f"newest first; grade like them:\n<picked>\n{picked}\n</picked>\n<passed>\n{passed}\n</passed>"
                     + f"\n\nJobs that answered their application with an interview or offer, and jobs that rejected it, "
                       f"newest first; score jobs like the answered ones higher:\n<answered>\n{answered}\n</answered>\n"
                       f"<rejected>\n{rejected}\n</rejected>"}]


def save_grades(conn, rows: list[tuple]) -> None:
    """One row per job (job_id is the key): a job graded again overwrites its grade and stamps graded_at."""
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO core.claude_grade (job_id, score, cv, reason, restriction, model)"
                        " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (job_id) DO UPDATE SET score = EXCLUDED.score,"
                        " cv = EXCLUDED.cv, reason = EXCLUDED.reason, restriction = EXCLUDED.restriction,"
                        " model = EXCLUDED.model, graded_at = now()", rows)


def save_deadlines(conn, rows: list[tuple]) -> None:
    """An "Apply by" deadline (core.deadline, the Today page) for each (job_id, closing date) not yet passed, once per
    job; a date that is not YYYY-MM-DD is skipped."""
    rows = [(job_id, day) for job_id, day in rows if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day)]
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO core.deadline (job_id, what, due, note) SELECT %s, 'Apply by', %s::date, 'from the posting'"
                        " WHERE %s::date >= current_date AND NOT EXISTS (SELECT 1 FROM core.deadline WHERE job_id = %s"
                        " AND what = 'Apply by')", [(job_id, day, day, job_id) for job_id, day in rows])


def alert_fresh(conn, job_ids: list[int]) -> None:
    """A phone push (digest.push) for each job just graded 8 or more that is still in its first 12 hours since it was
    posted: the ones worth applying to today. A push that fails is a warning; the grades are kept."""
    for title, company, score, url in conn.execute(
            "SELECT j.title, j.company, g.score, coalesce(j.apply_url, j.job_url) FROM core.claude_grade g JOIN core.job j"
            " USING (job_id) WHERE j.job_id = ANY(%s) AND g.score >= 8 AND now() - j.posted_at <= interval '12 hours'",
            (job_ids,)).fetchall():
        try:
            push(f"{score}/10: {title}", f"{company}, posted in the last 12 hours", url)
        except Exception as e:  # the network or ntfy.sh: the grades matter more than the push
            print(f"WARNING grade_claude: push for {company}: {e!r}"[:200])


def grade_claude(conn) -> None:
    cfg = SETTINGS["claude"]
    if not FEATURES["grade"]:
        print("grade_claude: off in settings.yaml (features.grade)")
        return
    keys = [key for _, key, *_ in llm.PROVIDERS] + ["ANTHROPIC_API_KEY"]
    if not any(os.environ.get(key) for key in keys):
        print(f"grade_claude: none of {', '.join(keys)} in .env, skipped")
        return
    cvs = conn.execute("SELECT label, text FROM core.cv ORDER BY label").fetchall()
    if not cvs:
        print("grade_claude: no CV uploaded in the tracker, skipped")
        return
    jobs = jobs_to_grade(conn)
    eligibility = FEATURES["eligibility"]
    system = system_blocks(cvs, recent_clicks(conn), eligibility, replies(conn))
    text = "".join(block["text"] for block in system)
    used = Counter(dict(conn.execute("SELECT model, count(*) FROM core.claude_grade WHERE graded_at >= current_date"
                                     " GROUP BY model").fetchall()))  # each free model's calls today, for its cap
    import anthropic  # for its errors; the SDK loads only in a run that calls Claude
    client = claude.client() if os.environ.get("ANTHROPIC_API_KEY") else None  # gives up well inside the DAG's 600 s
    out_of_credit = False

    def grade(job: tuple) -> tuple | None:
        nonlocal out_of_credit
        job_id, title, company, location, description, years = job
        if time_left(GRADE_SECONDS) < 30:
            return None
        user = (f"{title} at {company} ({location})" + (f"\nYears of experience it asks for: {years}+" if years else "")
                + f"\n\n{description[:6000]}")
        if free := llm.ask_json(text, user, Grade, used):
            model, g = free
            return row(job_id, g, model)
        if out_of_credit or client is None:  # no free model answered and Claude cannot: tried again next run
            return None
        try:
            r = client.messages.parse(
                model=cfg["model"], max_tokens=MAX_TOKENS, system=system, output_format=Grade,
                output_config={"effort": "low"}, messages=[{"role": "user", "content": user}])
        except anthropic.BadRequestError as e:
            if claude.out_of_credit(e):  # the balance ran out: every call would be refused
                out_of_credit = True
            else:  # one job refused: a warning, never the loss of the grades this run already paid for
                print(f"WARNING grade_claude: job {job_id} refused: {e!r}"[:300])
            return None
        except anthropic.APIError as e:  # one job down is a warning: the emails still go out
            print(f"WARNING grade_claude: job {job_id}: {e!r}"[:300])
            return None
        return row(job_id, r.parsed_output, cfg["model"]) if r.parsed_output else None

    def row(job_id: int, g: Grade, model: str) -> tuple:
        return (job_id, max(0, min(10, g.score)), g.cv, g.reason, (g.restriction.strip() or None) if eligibility else None,
                model, g.years_required if 0 < g.years_required <= 20 else None, g.apply_by.strip())

    # the first job alone writes the cache; only then do the other calls start, so they all read it
    first = [grade(jobs[0])] if jobs else []
    with ThreadPoolExecutor(4) as pool:
        done = [row for row in [*first, *pool.map(grade, jobs[1:])] if row]
    save_grades(conn, [row[:6] for row in done])
    alert_fresh(conn, [row[0] for row in done])
    save_deadlines(conn, [(row[0], row[7]) for row in done if row[1] >= 8 and not row[4] and row[7]])
    with conn.cursor() as cur:  # the description's own digits (steps.years_required) win over Claude's reading
        cur.executemany("UPDATE core.job SET years_required = %s WHERE job_id = %s AND years_required IS NULL",
                        [(row[6], row[0]) for row in done if row[6]])
    conn.commit()
    by_model = Counter(row[5] for row in done)
    print(f"grade_claude: {len(done)} of {len(jobs)} jobs graded ("
          + ", ".join(f"{model} {n}" for model, n in by_model.most_common()) + ")"
          + (f"; free models failed: {', '.join(f'{why} x{n}' for why, n in llm.FAILED.most_common())}" if llm.FAILED else "")
          + (" - WARNING the API credit ran out: no more grades until you buy more" if out_of_credit else ""))
    if jobs and not done:
        raise RuntimeError(f"grade_claude: 0 of {len(jobs)} jobs graded - check the API key, credit and network")
