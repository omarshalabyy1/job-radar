r"""The weekly ops review (job_radar/review.py): every figure in the report is one read-only query on the warehouse, over the
seven days that end at `now`. Every row is written inside a transaction that is rolled back at the end, so the warehouse is
left as it was; without the warehouse these tests are skipped. Run: python -m pytest tests/test_review.py
"""

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import psycopg
import pytest

from job_radar import db, review
from job_radar.config import AI_PLACES

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
DAY = timedelta(days=1)


@pytest.fixture
def conn():
    try:
        connection = db.connect()
    except (KeyError, psycopg.OperationalError) as e:
        pytest.skip(f"no warehouse: {e!r}"[:120])
    # an empty week to start from, whatever other tests committed: the rows go inside this transaction, so they come back
    for table in ("core.step_run", "core.application", "core.claude_grade", "core.application_email"):
        connection.execute(f"DELETE FROM {table}")
    yield connection
    connection.rollback()  # nothing the test wrote stays
    connection.close()


def add_job(conn, key: str, description: str | None = "Build pipelines", place: str = AI_PLACES[0], age: int = 0) -> int:
    return conn.execute(
        "INSERT INTO core.job (job_key, title, company, location, place, source, job_url, role_rank, role,"
        " target_company, description, first_seen, restriction) VALUES (%s, 'Data Engineer', 'Acme', 'Cairo', %s,"
        " 'test', %s, 1, 'Data Engineer', false, %s, current_date - %s, NULL) RETURNING job_id",
        (f"test_review {key}", place, f"https://test.example/{key}", description, age)).fetchone()[0]


def add_step(conn, step: str, status: str, started_at: datetime, cost: str | None = None) -> None:
    conn.execute("INSERT INTO core.step_run (step, started_at, status, cost_usd) VALUES (%s, %s, %s, %s)",
                 (step, started_at, status, None if cost is None else Decimal(cost)))


def add_grade(conn, key: str, score: int, graded_at: datetime) -> None:
    conn.execute("INSERT INTO core.claude_grade (job_id, score, cv, reason, model, graded_at)"
                 " VALUES (%s, %s, 'Data Analyst', 'r', 'm', %s)", (add_job(conn, key), score, graded_at))


def add_application(conn, key: str, status: str, updated_at: datetime) -> None:
    conn.execute("INSERT INTO core.application (job_id, status, updated_at) VALUES (%s, %s, %s)",
                 (add_job(conn, key), status, updated_at))


def add_email(conn, message_id: str, kind: str, received_at: datetime) -> None:
    conn.execute("INSERT INTO core.application_email (message_id, received_at, sender, subject, kind)"
                 " VALUES (%s, %s, 'hr@example.com', 'Re: your application', %s)", (message_id, received_at, kind))


def seed_steps(conn) -> None:
    add_step(conn, "grade_claude", "ok", NOW - DAY, "0.30")
    add_step(conn, "grade_claude", "failed", NOW - 2 * DAY)  # no cost: a failed run made no calls
    add_step(conn, "tailor", "warn", NOW - DAY, "0.07")


def test_counts_runs_failures_and_cost_per_step(conn):
    seed_steps(conn)
    text = review.render(conn, NOW)
    assert "| grade_claude | 2 | 1 | 0 | 0.30 |" in text
    assert "| tailor | 1 | 0 | 1 | 0.07 |" in text


def test_window_is_seven_days_and_excludes_the_edge(conn):
    add_step(conn, "edge_step", "ok", NOW - 7 * DAY)  # exactly 7 days old: outside
    add_step(conn, "edge_step", "ok", NOW - 7 * DAY + timedelta(minutes=1))  # a minute younger: inside
    text = review.render(conn, NOW)
    assert "| edge_step | 1 | 0 | 0 | 0.00 |" in text  # only the younger row is counted


def test_a_row_after_now_is_outside_the_window(conn):
    add_step(conn, "future_step", "ok", NOW + timedelta(minutes=1))
    add_step(conn, "now_step", "ok", NOW)  # exactly now: inside
    text = review.render(conn, NOW)
    assert "future_step" not in text
    assert "| now_step | 1 | 0 | 0 | 0.00 |" in text


def test_running_rows_are_not_failures_and_null_cost_is_zero(conn):
    add_step(conn, "slow_step", "running", NOW - timedelta(hours=1))  # cost_usd NULL
    assert "| slow_step | 1 | 0 | 0 | 0.00 |" in review.render(conn, NOW)


def test_least_reliable_and_most_expensive_lines(conn):
    seed_steps(conn)
    text = review.render(conn, NOW)
    assert "Least reliable step: grade_claude (1 failed, 0 warned)" in text
    assert "Most expensive step: grade_claude ($0.30)" in text


def test_ties_break_by_step_name(conn):
    add_step(conn, "b_step", "failed", NOW - DAY, "0.10")
    add_step(conn, "a_step", "failed", NOW - DAY, "0.10")
    text = review.render(conn, NOW)
    assert "Least reliable step: a_step (1 failed, 0 warned)" in text
    assert "Most expensive step: a_step ($0.10)" in text


def test_the_report_adds_the_steps_up_to_a_total(conn):
    seed_steps(conn)
    assert "| total | 3 | 1 | 1 | 0.37 |" in review.render(conn, NOW)


def test_an_empty_week_says_so(conn):
    text = review.render(conn, NOW)
    assert "No runs in the last 7 days." in text
    assert "Least reliable step: none" in text
    assert "Most expensive step: none" in text


def test_grades_funnel_and_replies_are_counted_in_the_window(conn):
    for key, score in (("g8", 8), ("g9", 9), ("g5", 5)):
        add_grade(conn, key, score, NOW - DAY)
    add_grade(conn, "g_old", 10, NOW - 8 * DAY)  # outside the window
    for key, status in (("a1", "applied"), ("a2", "applied"), ("i1", "interview")):
        add_application(conn, key, status, NOW - DAY)
    add_application(conn, "a_old", "offer", NOW - 8 * DAY)
    add_email(conn, "m1", "interview", NOW - DAY)
    add_email(conn, "m2", "rejected", NOW - 2 * DAY)
    add_email(conn, "m_old", "rejected", NOW - 9 * DAY)
    text = review.render(conn, NOW)
    assert "Jobs graded 8 or higher this week: 2" in text
    assert "Applications moved this week: applied 2, interview 1" in text
    assert "Replies this week: interview 1, rejected 1" in text


def test_a_confirmation_receipt_and_an_unclassified_email_are_not_replies(conn):
    add_email(conn, "m1", "applied", NOW - DAY)  # the board confirming Omar's own application
    add_email(conn, "m2", "other", NOW - DAY)
    add_email(conn, "m3", "viewed", NOW - DAY)
    assert "Replies this week: viewed 1" in review.render(conn, NOW)


def test_an_empty_week_has_no_funnel_or_replies(conn):
    text = review.render(conn, NOW)
    assert "Jobs graded 8 or higher this week: 0" in text
    assert "Applications moved this week: none" in text
    assert "Replies this week: none" in text


def test_parked_questions_line_when_the_applier_tables_do_not_exist(conn):
    conn.execute("DROP TABLE IF EXISTS core.apply_question")  # inside the transaction: rolled back with the rest
    assert "Parked questions unanswered: Applier not built" in review.render(conn, NOW)


def test_parked_questions_are_counted_when_the_table_exists(conn):
    job_id = add_job(conn, "parked")
    conn.execute("DELETE FROM core.apply_question")
    conn.execute("INSERT INTO core.apply_question (job_id, question) VALUES (%s, 'Salary?'), (%s, 'Visa?')",
                 (job_id, job_id))
    conn.execute("INSERT INTO core.apply_question (job_id, question, answer, answered_at)"
                 " VALUES (%s, 'Notice period?', '1 month', now())", (job_id,))
    assert "Parked questions unanswered: 2" in review.render(conn, NOW)


def test_the_report_names_the_tables_it_read(conn):
    assert review.render(conn, NOW).rstrip().endswith(
        "Read-only: core.step_run, core.application, core.claude_grade, core.application_email")


def test_rendering_writes_nothing_to_the_warehouse(conn):
    seed_steps(conn)
    before = conn.execute("SELECT count(*) FROM core.step_run").fetchone()[0]
    review.render(conn, NOW)
    assert conn.execute("SELECT count(*) FROM core.step_run").fetchone()[0] == before


def test_writing_twice_gives_the_same_file(conn, tmp_path):
    seed_steps(conn)
    first = review.write_weekly(conn, NOW, tmp_path)
    content = first.read_text(encoding="utf-8")
    assert review.write_weekly(conn, NOW, tmp_path) == first
    assert first.name == "weekly-2026-11-08.md" and first.read_text(encoding="utf-8") == content


def test_write_weekly_creates_the_folder(conn, tmp_path):
    path = review.write_weekly(conn, NOW, tmp_path / "output" / "reviews")
    assert path.parent == tmp_path / "output" / "reviews" and path.exists()


def test_main_prints_the_path_and_returns_0(conn, tmp_path, monkeypatch, capsys):  # conn: skips without a warehouse
    monkeypatch.setattr(review, "OUTPUT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["review", "weekly"])
    assert review.main() == 0
    printed = capsys.readouterr().out.strip()
    assert printed.startswith(str(tmp_path / "reviews" / "weekly-")) and printed.endswith(".md")


@pytest.mark.parametrize("argv", [["review"], ["review", "daily"], ["review", "weekly", "extra"]])
def test_main_with_no_or_another_argument_prints_usage_and_returns_2(argv, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", argv)
    assert review.main() == 2
    assert "usage" in capsys.readouterr().out.lower()
