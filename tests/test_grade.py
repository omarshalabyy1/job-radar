r"""Which jobs Claude grades (and grades again after a new CV) and how a grade is saved, against the warehouse
(docker-compose up; WAREHOUSE_PASSWORD in .env). Every row is written inside a transaction that is rolled
back at the end, so the warehouse is left as it was; without the warehouse these tests are skipped. Run on the
laptop: .venv\Scripts\python -m pytest tests
"""

from pathlib import Path

import anthropic
import psycopg
import pytest
from dotenv import load_dotenv

from job_radar import claude, db, grade
from job_radar.config import AI_PLACES, KEEP_DAYS

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

HOUR = "interval '1 hour'"


@pytest.fixture
def conn(monkeypatch):
    monkeypatch.setitem(grade.FEATURES, "eligibility", True)
    try:
        connection = db.connect()
    except (KeyError, psycopg.OperationalError) as e:
        pytest.skip(f"no warehouse: {e!r}"[:120])
    yield connection
    connection.rollback()  # nothing the test wrote stays
    connection.close()


def add_job(conn, key: str, description: str | None = "Build pipelines", place: str = AI_PLACES[0],
            age: int = 0, restriction: str | None = None) -> int:
    return conn.execute(
        "INSERT INTO core.job (job_key, title, company, location, place, source, job_url, role_rank, role,"
        " target_company, description, first_seen, restriction) VALUES (%s, 'Data Engineer', 'Acme', 'Cairo', %s,"
        " 'test', %s, 1, 'Data Engineer', false, %s, current_date - %s, %s) RETURNING job_id",
        (f"test_grade {key}", place, f"https://test.example/{key}", description, age, restriction)).fetchone()[0]


def add_grade(conn, job_id: int, graded_at_sql: str) -> None:
    conn.execute(f"INSERT INTO core.claude_grade (job_id, score, cv, reason, model, graded_at)"
                 f" VALUES (%s, 5, 'Data Analyst', 'old', 'm', {graded_at_sql})", (job_id,))


def queue(conn, mine: dict[str, int]) -> set[str]:
    """The names of my test jobs that the selection returns (the warehouse's own jobs are ignored)."""
    ids = {job[0] for job in grade.jobs_to_grade(conn, 10_000)}
    return {name for name, job_id in mine.items() if job_id in ids}


def test_a_job_is_graded_when_it_has_no_fresh_grade_and_nothing_bars_it(conn):
    # a CV uploaded a day from now: every grade written before it is stale
    conn.execute("INSERT INTO core.cv (label, filename, text, uploaded_at) VALUES ('zz test', 'zz.pdf', 'sql', now() + interval '1 day')")
    cv = "(SELECT max(uploaded_at) FROM core.cv)"
    mine = {name: add_job(conn, name, **kw) for name, kw in {
        "new": {}, "age_at_the_window_edge": {"age": KEEP_DAYS}, "saved": {},
        "stale": {}, "graded_exactly_at_the_cv": {}, "graded_after_the_cv": {},
        "no_description": {"description": None}, "other_place": {"place": "Atlantis"},
        "too_old": {"age": KEEP_DAYS + 1}, "restricted": {"restriction": "US citizens only"},
        "ignored": {}, "rejected": {}}.items()}
    add_grade(conn, mine["stale"], f"{cv} - {HOUR}")
    add_grade(conn, mine["graded_exactly_at_the_cv"], cv)
    add_grade(conn, mine["graded_after_the_cv"], f"{cv} + {HOUR}")
    for name in ("saved", "ignored", "rejected"):
        conn.execute("INSERT INTO core.application (job_id, status) VALUES (%s, %s)", (mine[name], name))
    assert queue(conn, mine) == {"new", "age_at_the_window_edge", "saved", "stale"}
    # features.eligibility off: a regex flag no longer keeps a job from being graded
    grade.FEATURES["eligibility"] = False
    assert "restricted" in queue(conn, mine)
    assert len(grade.jobs_to_grade(conn, 1)) == 1  # the limit


def test_a_job_graded_before_your_newest_cv_is_graded_again_once_and_only_once(conn):
    conn.execute("INSERT INTO core.cv (label, filename, text, uploaded_at) VALUES ('zz test', 'zz.pdf', 'sql', now())")  # newest CV: this transaction's start
    job = add_job(conn, "regrade")
    add_grade(conn, job, f"now() - {HOUR}")
    before = conn.execute("SELECT count(*) FROM core.claude_grade WHERE job_id = %s", (job,)).fetchone()[0]
    assert job in {row[0] for row in grade.jobs_to_grade(conn, 10_000)}
    regrade = [(job, 9, "AI & Data Engineer", "new CV fits", None, "claude-sonnet-5-5")]
    grade.save_grades(conn, regrade)
    grade.save_grades(conn, regrade)  # the second run changes nothing
    rows = conn.execute("SELECT count(*), sum(score), min(reason), min(cv), min(model), bool_and(graded_at = now())"
                        " FROM core.claude_grade WHERE job_id = %s", (job,)).fetchone()
    assert before == 1 and rows == (1, 9, "new CV fits", "AI & Data Engineer", "claude-sonnet-5-5", True)
    # graded_at = the CV's uploaded_at counts as fresh (>=), so the job leaves the queue and is not graded every run
    assert job not in {row[0] for row in grade.jobs_to_grade(conn, 10_000)}


class FakeConn:
    """Enough of a connection for grade_claude: one CV, a commit, no database."""

    def execute(self, *_):
        return self

    def fetchall(self):
        return [("Data Analyst", "sql")]

    def commit(self):
        pass


class FakeClient:
    """messages.parse returns a grade for a job unless its title says to fail, with no network."""

    def __init__(self, *_, **__):
        self.messages = self

    def parse(self, messages, **_):
        if "fail" in messages[0]["content"]:
            raise anthropic.APIConnectionError(request=None)
        for word, text in (("refuse", "prompt is too long"), ("credit", "Your credit balance is too low")):
            if word in messages[0]["content"]:
                error = anthropic.BadRequestError.__new__(anthropic.BadRequestError)  # no response object needed
                Exception.__init__(error, text)
                raise error
        usage = type("Usage", (), {"input_tokens": 1, "cache_read_input_tokens": 0,
                                   "cache_creation_input_tokens": 0, "output_tokens": 1})
        return type("Reply", (), {"parsed_output": grade.Grade(score=5, cv="Data Analyst", reason="ok", restriction=""),
                                  "usage": usage})


def run_grade(monkeypatch, titles: list[str]) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setitem(grade.FEATURES, "grade", True)
    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    monkeypatch.setattr(grade, "recent_clicks", lambda conn: [])
    monkeypatch.setattr(grade, "save_grades", lambda conn, rows: None)
    monkeypatch.setattr(grade, "jobs_to_grade", lambda conn, limit: [(i, t, "Acme", "Cairo", "text") for i, t in enumerate(titles)])
    grade.grade_claude(FakeConn())


def test_grade_claude_fails_when_it_selected_jobs_and_graded_none(monkeypatch, capsys):
    with pytest.raises(RuntimeError, match="0 of 2 jobs graded"):
        run_grade(monkeypatch, ["fail one", "fail two"])
    assert "0 of 2 jobs graded with" in capsys.readouterr().out  # the usual summary line is printed first


def test_grade_claude_stays_green_with_nothing_to_grade_or_some_graded(monkeypatch, capsys):
    run_grade(monkeypatch, [])
    run_grade(monkeypatch, ["fail one", "good one"])
    assert "1 of 2 jobs graded with" in capsys.readouterr().out


def test_a_refused_job_is_a_warning_and_the_other_grades_are_kept(monkeypatch, capsys):
    run_grade(monkeypatch, ["refuse one", "good one"])  # a 400 that is not about credit must not lose the paid grade
    out = capsys.readouterr().out
    assert "WARNING grade_claude: job 0 refused" in out and "1 of 2 jobs graded with" in out
    with pytest.raises(RuntimeError, match="0 of 2 jobs graded"):  # but a run where every job is refused still fails
        run_grade(monkeypatch, ["refuse one", "refuse two"])


def test_no_credit_stops_the_rest_without_a_warning_per_job(monkeypatch, capsys):
    run_grade(monkeypatch, ["good one", "credit two", "good three"])  # the first job is graded alone; the empty balance stops the rest
    out = capsys.readouterr().out
    assert "refused" not in out and "the API credit ran out" in out


def test_the_one_claude_client_gives_up_after_120_seconds_and_tries_once_more(monkeypatch):
    made = []
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: made.append(kwargs) or "client")
    assert claude.client() == "client" and made == [{"timeout": 120, "max_retries": 1}]  # grade, tailor and applications all use it


def test_an_empty_balance_is_told_from_any_other_error():
    assert claude.out_of_credit(Exception("Your credit balance is too low to access the Anthropic API"))
    assert not claude.out_of_credit(Exception("prompt is too long"))


def test_recent_clicks_leave_out_the_saved_rows_the_tailor_writes(conn):
    """tailor saves a job with the note 'draft ready <day>': that is not a pick of yours. A saved job you saved
    yourself (no such note) still is, and so is one you went on to apply to."""
    rows = {}
    for name, status, note in (("tailor_saved", "saved", "draft ready 2026-10-11"), ("own_save", "saved", None),
                               ("own_save_with_note", "saved", "good fit"), ("applied_after_draft", "applied", "draft ready 2026-10-11"),
                               ("ignored", "ignored", None)):
        job_id = conn.execute(
            "INSERT INTO core.job (job_key, title, company, location, place, source, job_url, role_rank, role, target_company, first_seen)"
            " VALUES (%s, %s, 'Acme', 'Cairo', 'Egypt', 'test', %s, 1, 'Data Engineer', false, current_date) RETURNING job_id",
            (f"test_grade {name}", f"Zz {name}", f"https://test.example/{name}")).fetchone()[0]
        conn.execute("INSERT INTO core.application (job_id, status, note, updated_at) VALUES (%s, %s, %s, now() + interval '1 day')",
                     (job_id, status, note))
        rows[name] = job_id
    lines = [line for line, _ in grade.recent_clicks(conn) if line.startswith("Zz ")]
    assert sorted(lines) == ["Zz applied_after_draft at Acme -> applied", "Zz ignored at Acme -> ignored",
                             "Zz own_save at Acme -> saved", "Zz own_save_with_note at Acme -> saved"]
