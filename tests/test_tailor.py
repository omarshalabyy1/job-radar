r"""Where tailor writes a job's drafts and how the tracker's Ready to apply tab finds them again. Run on the
laptop: .venv\Scripts\python -m pytest tests
"""

from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv

from job_radar import db, tailor
from job_radar.config import AI_PLACES, OUTPUT
from job_radar.tailor import draft_day, draft_folder

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def test_draft_folder_is_a_clean_name_under_the_day():
    folder = draft_folder("2026-10-11", "Acme Ltd.", "Senior Data Engineer (BI)", 7)
    assert folder.relative_to(OUTPUT).as_posix() == "applications/2026-10-11/acme-ltd-senior-data-engineer-bi"


def test_draft_folder_is_cut_at_80_characters_without_a_trailing_dash():
    name = draft_folder("2026-10-11", "Acme", "Data " * 30, 7).name
    assert len(name) <= 80 and not name.endswith("-")


def test_draft_folder_without_letters_or_digits_is_the_job_id():
    assert draft_folder("2026-10-11", "", "—", 42).name == "job-42"


def test_draft_day_from_the_note():
    assert draft_day("draft ready 2026-10-11") == "2026-10-11"
    assert draft_day("draft ready 2026-10-11; from your mailbox 2026-10-12") == "2026-10-11"  # a mailbox note added
    assert draft_day("draft ready") is None
    assert draft_day("called them") is None
    assert draft_day(None) is None


class Untouched:
    """A connection that fails on any use: a step that stops at its switch must not read or write the warehouse."""

    def __getattr__(self, name):
        raise AssertionError(f"the warehouse was used: {name}")


def test_tailor_stops_at_its_switch_before_touching_anything(monkeypatch, capsys):
    monkeypatch.setitem(tailor.SETTINGS, "features", {"tailor": False})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    tailor.tailor(Untouched())
    assert capsys.readouterr().out.strip() == "tailor: off in settings.yaml (features.tailor)"
    monkeypatch.setitem(tailor.SETTINGS, "features", {})  # the switch missing counts as off
    tailor.tailor(Untouched())
    assert "off in settings.yaml" in capsys.readouterr().out


def test_tailor_without_a_key_or_a_cv_stops_green_and_says_why(monkeypatch, capsys):
    class NoCv:
        def execute(self, *_):
            return self

        def fetchall(self):
            return []

    monkeypatch.setitem(tailor.SETTINGS, "features", {"tailor": True})
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    tailor.tailor(Untouched())
    assert capsys.readouterr().out.strip() == "tailor: no ANTHROPIC_API_KEY in .env, skipped"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    tailor.tailor(NoCv())
    assert capsys.readouterr().out.strip() == "tailor: no CV uploaded in the tracker, skipped"


# ---- which jobs are drafted, against the warehouse (rolled back; skipped without it) ----

@pytest.fixture
def conn(monkeypatch):
    monkeypatch.setitem(tailor.FEATURES, "eligibility", True)
    try:
        connection = db.connect()
    except (KeyError, psycopg.OperationalError) as e:
        pytest.skip(f"no warehouse: {e!r}"[:120])
    yield connection
    connection.rollback()  # nothing the test wrote stays
    connection.close()


def add_graded_job(conn, key: str, score: int, place: str = AI_PLACES[0], restriction: str | None = None,
                   grade_restriction: str | None = None, role_rank: int = 0) -> int:
    job_id = conn.execute(
        "INSERT INTO core.job (job_key, title, company, location, place, source, job_url, role_rank, role, target_company,"
        " first_seen, restriction) VALUES (%s, 'Data Engineer', 'Acme', 'Cairo', %s, 'test', %s, %s, 'Data Engineer', false,"
        " current_date, %s) RETURNING job_id", (f"test_tailor {key}", place, f"https://test.example/{key}", role_rank, restriction)).fetchone()[0]
    conn.execute("INSERT INTO core.claude_grade (job_id, score, cv, reason, model, restriction) VALUES (%s, %s, 'cv', 'why', 'm', %s)",
                 (job_id, score, grade_restriction))
    return job_id


def picked(conn, mine: dict[str, int]) -> list[str]:
    """The names of my test jobs the selection returns, in its order (the warehouse's own jobs are ignored)."""
    names = {job_id: name for name, job_id in mine.items()}
    return [names[row[0]] for row in tailor.jobs_to_tailor(conn, 10_000) if row[0] in names]


def test_tailor_drafts_the_best_graded_jobs_nothing_restricts_and_you_have_not_touched(conn):
    mine = {name: add_graded_job(conn, name, **kw) for name, kw in {
        "nine": {"score": 9}, "eight": {"score": 8}, "ten": {"score": 10}, "seven": {"score": 7},
        "elsewhere": {"score": 10, "place": "Atlantis"}, "has_a_row": {"score": 10}, "ignored": {"score": 10},
        "regex_bars_it": {"score": 10, "restriction": "US citizens only"},
        "claude_bars_it": {"score": 10, "grade_restriction": "needs a US work permit"}}.items()}
    for name in ("has_a_row", "ignored"):  # any status of yours keeps the job out: you decide, not the draft
        conn.execute("INSERT INTO core.application (job_id, status) VALUES (%s, %s)", (mine[name], "saved" if name == "has_a_row" else "ignored"))
    assert picked(conn, mine) == ["ten", "nine", "eight"]  # best score first; 8 is in, 7 is out
    tailor.FEATURES["eligibility"] = False  # features.eligibility off: restrictions no longer keep a job from a draft
    assert sorted(picked(conn, mine)) == ["claude_bars_it", "eight", "nine", "regex_bars_it", "ten"]
    assert len(tailor.jobs_to_tailor(conn, 1)) == 1 and tailor.MIN_SCORE == 8  # the limit; the bar


def test_tailor_ties_on_score_go_to_the_role_you_ranked_first_then_the_older_job(conn):
    mine = {"worse_role": add_graded_job(conn, "worse_role", 9, role_rank=2), "better_role": add_graded_job(conn, "better_role", 9, role_rank=1),
            "better_role_later": add_graded_job(conn, "better_role_later", 9, role_rank=1)}
    assert picked(conn, mine) == ["better_role", "better_role_later", "worse_role"]
