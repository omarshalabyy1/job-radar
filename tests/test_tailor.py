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
    return [names[row[0]] for row in tailor.jobs_to_tailor(conn) if row[0] in names]


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
    assert tailor.MIN_SCORE == 8  # the bar


def test_tailor_ties_on_score_go_to_the_role_you_ranked_first_then_the_older_job(conn):
    mine = {"worse_role": add_graded_job(conn, "worse_role", 9, role_rank=2), "better_role": add_graded_job(conn, "better_role", 9, role_rank=1),
            "better_role_later": add_graded_job(conn, "better_role_later", 9, role_rank=1)}
    assert picked(conn, mine) == ["better_role", "better_role_later", "worse_role"]


def sample_draft(**changes) -> tailor.Draft:
    fields = dict(
        name="Zz Candidate", title_line="Data Engineer | Pipelines",
        contact=[tailor.Link(text="Rehab, Cairo, Egypt", url=""), tailor.Link(text="zz@example.com", url="mailto:zz@example.com"),
                 tailor.Link(text="Portfolio: omarlabs.dev", url="https://omarlabs.dev")],
        summary="Data Engineer with 2 years of experience building pipelines in Python and SQL.",
        skills=[tailor.SkillGroup(label="Data", items="SQL, Python")],
        experience=[tailor.Entry(title="Data Engineer", org="Acme | Cairo", when="2025 – Present (1 yr)",
                                 bullets=["Cut a report from 3 days to 2 hours."], technologies="", repo_url="")],
        projects=[tailor.Entry(title="Warehouse", org="Star schema", when="2026", bullets=["Built it."],
                               technologies="PostgreSQL", repo_url="https://github.com/zz/warehouse")],
        education=[], certifications=[], languages="Arabic, English", file_tag="de",
        cover_letter_md="Dear team,\n\nI build pipelines your analysts trust.\n\nZz Candidate", notes_md="notes")
    return tailor.Draft(**{**fields, **changes})


def test_a_draft_is_written_as_html_and_one_page_pdfs_with_their_links(monkeypatch, tmp_path):
    problems = tailor.write_draft(tmp_path, sample_draft(), "Acme Ltd.")
    assert problems == []
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["Zz Candidate Acme Ltd. DE Cover Letter.pdf", "Zz Candidate Acme Ltd. DE.pdf", "cover-letter.html",
                     "cover-letter.md", "cv.html", "cv.md", "notes.md"]
    cv = (tmp_path / "cv.html").read_text(encoding="utf-8")
    assert 'Portfolio: <a href="https://omarlabs.dev">omarlabs.dev</a>' in cv and '(<a href="https://github.com/zz/warehouse">GitHub</a>)' in cv


def test_a_cv_pdf_over_one_page_or_without_its_links_fails_its_check():
    long = sample_draft(experience=[tailor.Entry(title="Role", org="Acme", when="2025", bullets=["Built a pipeline. " * 30] * 40,
                                                 technologies="", repo_url="")])
    page = tailor.cv_html(long)
    assert any("pages, not 1" in p for p in tailor.pdf_problems(tailor.pdf_of(page), page))
    short = tailor.cv_html(sample_draft())
    no_links = tailor.pdf_of(short.replace("<a href=", "<span data-href="))  # printed without its links
    assert any("lost its links" in p for p in tailor.pdf_problems(no_links, short))


def test_a_cover_letter_that_names_a_gap_is_thrown_away(capsys):
    class Client:
        def __init__(self, draft):
            self.messages, self.draft = self, draft

        def parse(self, **_):
            return type("R", (), {"parsed_output": self.draft})()

    job = (1, "Data Engineer", "Acme", "Cairo", "text", 9, "cv", "fits")
    for letter in ("I have not used Spark yet.", "I have no experience with Kafka.", "I lack Airflow."):
        assert tailor.ask(Client(sample_draft(cover_letter_md=letter)), {"cv": "text"}, job) is None
    assert "names a gap" in capsys.readouterr().out
    assert tailor.ask(Client(sample_draft()), {"cv": "text"}, job) is not None
