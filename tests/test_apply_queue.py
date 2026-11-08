"""job_radar/apply/queue.py: which graded jobs the apply runner may touch, under the guardrails of docs/superpowers/plans/2026-11-08-auto-apply.md."""
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from job_radar import db
from job_radar.apply import queue

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
GH, LEVER, LI = "https://boards.greenhouse.io/acme/jobs/1", "https://jobs.lever.co/acme/1", "https://www.linkedin.com/jobs/view/1"
CFG = {"enabled": True, "score_floor": 8, "max_years": 3, "fresh_hours": 48, "daily_total": 15, "per_board": 8,
       "board_caps": {"linkedin": 5, "indeed": 5}, "company_days": 14,
       "boards": ["greenhouse", "lever", "workable", "ashby", "linkedin", "indeed"]}


@pytest.fixture
def conn():
    try:
        connection = db.connect()
    except (KeyError, psycopg.OperationalError) as e:
        pytest.skip(f"no warehouse: {e!r}"[:120])
    yield connection
    connection.rollback()  # nothing the test wrote stays
    connection.close()


def job(conn, key, company="Acme", url=GH, score=9, restriction=None, grade_restriction=None, years=None, age_hours=1):
    job_id = conn.execute(
        "INSERT INTO core.job (job_key, title, company, location, place, source, job_url, role_rank, role, target_company, first_seen,"
        " posted_at, apply_url, restriction, years_required) VALUES (%s, 'Data Engineer', %s, 'Cairo', 'Egypt', 'test', %s, 1, 'Data Engineer',"
        " false, %s, %s, %s, %s, %s) RETURNING job_id",
        (key, company, url, NOW.date(), NOW - timedelta(hours=age_hours), url, restriction, years)).fetchone()[0]
    conn.execute("INSERT INTO core.claude_grade (job_id, score, cv, reason, model, restriction) VALUES (%s, %s, 'cv', 'ok', 'm', %s)",
                 (job_id, score, grade_restriction))
    return job_id


def picked(conn, **cfg):
    return [j.job_id for j in queue.pick(conn, NOW, {**CFG, **cfg})]


def test_board_of_names_the_board_from_the_url():
    assert queue.board_of("https://boards.greenhouse.io/x/jobs/1") == "greenhouse"
    assert queue.board_of("https://jobs.lever.co/x/1") == "lever"
    assert queue.board_of("https://apply.workable.com/x/j/1") == "workable"
    assert queue.board_of("https://www.linkedin.com/jobs/view/1") == "linkedin"
    assert queue.board_of("https://eg.indeed.com/viewjob?jk=1") == "indeed"
    assert queue.board_of("https://example.com/careers/1") == "other"


def test_a_strong_fresh_job_on_an_allowed_board_is_picked(conn):
    a = job(conn, "zz-a")
    assert a in picked(conn)


def test_score_below_the_floor_is_never_picked(conn):
    low = job(conn, "zz-low", score=7)
    assert low not in picked(conn)


def test_a_restriction_or_too_many_years_asked_is_never_picked(conn):
    barred = job(conn, "zz-barred", restriction="US only")
    graded_barred = job(conn, "zz-gbarred", grade_restriction="needs EU residency")
    senior = job(conn, "zz-senior", years=5)
    ok = job(conn, "zz-ok", years=3)
    got = picked(conn)
    assert ok in got and not {barred, graded_barred, senior} & set(got)


def test_stale_applied_or_ignored_or_already_tried_jobs_are_never_picked(conn):
    stale = job(conn, "zz-stale", age_hours=50)
    applied = job(conn, "zz-applied")
    conn.execute("INSERT INTO core.application (job_id, status) VALUES (%s, 'applied')", (applied,))
    tried = job(conn, "zz-tried")
    queue.record_attempt(conn, tried, "greenhouse", "parked")
    assert not {stale, applied, tried} & set(picked(conn))


def test_a_board_that_is_not_on_the_allow_list_is_never_picked(conn):
    other = job(conn, "zz-other", url="https://example.com/careers/1")
    ashby = job(conn, "zz-ashby", url="https://jobs.ashbyhq.com/acme/1")
    assert other not in picked(conn)
    assert ashby in picked(conn, boards=["ashby"])
    assert ashby not in picked(conn, boards=["greenhouse"])


def test_the_daily_total_and_per_board_caps_hold_with_attempts_already_made_today(conn):
    gh = [job(conn, f"zz-gh{i}", company=f"GhCo{i}") for i in range(4)]
    lv = [job(conn, f"zz-lv{i}", company=f"LvCo{i}", url=LEVER) for i in range(3)]
    done = job(conn, "zz-done", company="DoneCo", url=LEVER)
    queue.record_attempt(conn, done, "lever", "submitted", when=NOW)
    got = picked(conn, daily_total=4, per_board=2)  # 3 left in total; greenhouse 2, lever 1 left
    assert len(got) == 3
    assert len([g for g in got if g in gh]) <= 2 and len([g for g in got if g in lv]) <= 1


def test_linkedin_and_indeed_have_their_own_lower_cap(conn):
    li = [job(conn, f"zz-li{i}", company=f"LiCo{i}", url=LI) for i in range(7)]
    assert len([g for g in picked(conn) if g in li]) == 5


def test_one_company_gets_one_submit_per_14_days_and_one_per_batch(conn):
    first = job(conn, "zz-c1", company="SameCo")
    second = job(conn, "zz-c2", company="SameCo", url=LEVER)
    assert len({first, second} & set(picked(conn))) == 1  # one per batch
    queue.record_attempt(conn, first, "greenhouse", "submitted", when=NOW - timedelta(days=13))
    assert second not in picked(conn)
    conn.execute("UPDATE core.apply_attempt SET started_at = %s WHERE job_id = %s", (NOW - timedelta(days=15), first))
    assert second in picked(conn)


def test_the_kill_switch_setting_and_stop_file_pick_nothing(conn, tmp_path):
    a = job(conn, "zz-ks")
    assert picked(conn, enabled=False) == []
    stop = tmp_path / "apply.stop"
    stop.write_text("stop")
    assert queue.pick(conn, NOW, CFG, stop_file=stop) == []
    stop.unlink()
    assert a in [j.job_id for j in queue.pick(conn, NOW, CFG, stop_file=stop)]


def test_record_attempt_stores_the_row_and_returns_its_id(conn):
    a = job(conn, "zz-rec")
    attempt_id = queue.record_attempt(conn, a, "greenhouse", "submitted", answers={"q": "a"}, screenshot="s.png", confirmation="Thanks")
    row = conn.execute("SELECT job_id, board, outcome, answers, screenshot, confirmation FROM core.apply_attempt WHERE id = %s", (attempt_id,)).fetchone()
    assert row == (a, "greenhouse", "submitted", {"q": "a"}, "s.png", "Thanks")
