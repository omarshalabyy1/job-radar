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


def test_tailor_drafts_the_best_graded_jobs_nothing_restricts_and_you_have_not_touched(conn, monkeypatch):
    monkeypatch.setitem(tailor.SETTINGS, "apply", {"enabled": False})  # pin the old rule; settings.yaml may have the apply runner on
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
        cover_letter_md="Dear team,\n\nI build pipelines your analysts trust.\n\nZz Candidate", notes_md="notes",
        form_answers_md="**Why this role?**\nI build pipelines your analysts trust.")
    return tailor.Draft(**{**fields, **changes})


def test_a_draft_is_written_as_html_and_one_page_pdfs_with_their_links(monkeypatch, tmp_path):
    problems = tailor.write_draft(tmp_path, sample_draft(), "Acme Ltd.")
    assert problems == []
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["Zz Candidate Acme Ltd. DE Cover Letter.pdf", "Zz Candidate Acme Ltd. DE.pdf", "cover-letter.html",
                     "cover-letter.md", "cv.html", "cv.md", "forms.md", "notes.md"]
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
    assert tailor.pdf_problems(tailor.pdf_of(short), short) == []  # every heading is in the PDF's text
    no_text = tailor.pdf_of(short.replace("<h2>Core Skills</h2>", "<h2></h2>"))  # the heading is not printed
    assert any("no text an ATS can read for: Core Skills" in p for p in tailor.pdf_problems(no_text, short))


class Client:
    """messages.parse gives the draft, then the reviewed draft (reviewed when given one), with no network."""

    def __init__(self, draft, reviewed=None):
        self.messages, self.draft, self.reviewed = self, draft, reviewed

    def parse(self, output_format, **_):
        out = self.draft if output_format is tailor.Draft else tailor.Review(
            draft=self.reviewed or self.draft, changes=["Used the posting's word: dbt"], missing_keywords=["Kafka"])
        return type("R", (), {"parsed_output": out})()


def test_the_review_replaces_the_draft_and_its_changes_go_in_the_notes():
    job = (1, "Data Engineer", "Acme", "Cairo", "text", 9, "cv", "fits")
    d = tailor.ask(Client(sample_draft(), sample_draft(summary="Data Engineer building dbt pipelines.")), {"cv": "text"}, job)
    assert d.summary == "Data Engineer building dbt pipelines."
    assert "## Review\n- Used the posting's word: dbt" in d.notes_md and "Keywords the CV cannot show: Kafka" in d.notes_md


def test_a_cover_letter_that_names_a_gap_is_thrown_away(capsys):

    job = (1, "Data Engineer", "Acme", "Cairo", "text", 9, "cv", "fits")
    for letter in ("I have not used Spark yet.", "I have no experience with Kafka.", "I lack Airflow."):
        assert tailor.ask(Client(sample_draft(cover_letter_md=letter)), {"cv": "text"}, job) is None
    assert tailor.ask(Client(sample_draft(form_answers_md="I have no experience with Kafka.")), {"cv": "text"}, job) is None
    assert "names a gap" in capsys.readouterr().out
    assert tailor.ask(Client(sample_draft()), {"cv": "text"}, job) is not None


# ---- the page is read again before a draft: a closed job is not drafted (fetch mocked, no network) ----

class Page:
    def __init__(self, text, url="https://jobs.example/1"):
        self.text, self.url = text, url


def gone(status):
    import requests
    return requests.HTTPError(response=type("R", (), {"status_code": status})())


def test_a_page_that_says_closed_or_is_gone_is_closed_and_a_failure_or_a_hold_is_not(monkeypatch):
    from job_radar.sources import Held
    asked = []

    def fetch_with(answer):
        def fake(method, url, **kwargs):
            asked.append(url)
            if isinstance(answer, Exception):
                raise answer
            return answer
        monkeypatch.setattr(tailor, "fetch", fake)

    fetch_with(Page("<h1>This job is no longer available</h1>"))
    assert tailor.closed_now("https://jobs.example/1") == "no longer available"
    fetch_with(Page("ok", url="https://boards.greenhouse.io/acme/jobs/1?error=true"))  # Greenhouse's closed redirect
    assert tailor.closed_now("https://boards.greenhouse.io/acme/jobs/1") == "no longer open"
    for status in (404, 410):
        fetch_with(gone(status))
        assert tailor.closed_now("https://jobs.example/1") == "page gone"
    for answer in (Page("<h1>Data Engineer</h1> Apply now"), gone(500), Held("jobs.example is held"),
                   __import__("requests").ConnectionError("down")):
        fetch_with(answer)
        assert tailor.closed_now("https://jobs.example/1") is None  # open, or not known: the draft goes ahead
    asked.clear()
    for url in ("https://www.linkedin.com/jobs/view/1", "https://eg.indeed.com/viewjob?jk=1", "https://dailyremote.com/x", None):
        assert tailor.closed_now(url) is None
    assert asked == []  # never read


class NoCommit:
    """The fixture's connection with commit turned into nothing: the fixture rolls everything back."""

    def __init__(self, conn):
        self.conn = conn

    def __getattr__(self, name):
        return getattr(self.conn, name)

    def commit(self):
        pass


def test_a_job_closed_before_drafting_is_ignored_and_not_drafted(conn, monkeypatch, capsys):
    closed, open_ = add_graded_job(conn, "closed", 10), add_graded_job(conn, "open", 9)
    conn.execute("INSERT INTO core.cv (label, filename, text) VALUES ('zz-tailor-cv', 'cv.pdf', 'text')")
    mine = [j for j in tailor.jobs_to_tailor(conn) if j[0] in (closed, open_)]  # the warehouse's own jobs left out
    monkeypatch.setattr(tailor, "jobs_to_tailor", lambda c: mine)
    pages = {"https://test.example/closed": Page("<p>This position has been filled</p>"), "https://test.example/open": Page("Apply")}
    monkeypatch.setattr(tailor, "fetch", lambda method, url, **kw: pages[url])
    drafted = []
    monkeypatch.setattr(tailor, "ask", lambda client, cvs, job: drafted.append(job[0]))  # None: nothing written
    monkeypatch.setattr(tailor.claude, "client", lambda: object())
    monkeypatch.setitem(tailor.SETTINGS, "features", {"tailor": True})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    tailor.tailor(NoCommit(conn))
    assert drafted == [open_]  # the open job only: no Claude call for the closed one
    row = conn.execute("SELECT status, note FROM core.application WHERE job_id = %s", (closed,)).fetchone()
    assert row[0] == "ignored" and row[1].startswith("closed before drafting ")
    assert conn.execute("SELECT count(*) FROM core.application WHERE job_id = %s", (open_,)).fetchone()[0] == 0
    assert f"tailor: job {closed} (Acme) is closed (" in capsys.readouterr().out


def test_the_ready_page_lists_apply_today_first_then_the_rest_by_score(conn):
    """Apply today: scored 8+ and posted within 48 hours (mart.job_status.fresh_level 1-3); then the rest, best score first."""
    from tracker.ready_tab import DRAFTS
    mine = {}
    for name, score, hours in (("old_ten", 10, 100), ("fresh_eight", 8, 30), ("fresh_nine", 9, 2), ("fresh_seven", 7, 1),
                               ("old_nine", 9, 60)):
        mine[name] = add_graded_job(conn, name, score)
        conn.execute("UPDATE core.job SET posted_at = now() - make_interval(hours => %s) WHERE job_id = %s", (hours, mine[name]))
        conn.execute("INSERT INTO core.application (job_id, status, note) VALUES (%s, 'saved', 'draft ready 2026-10-30')", (mine[name],))
    names = {job_id: name for name, job_id in mine.items()}
    rows = [(names[r[0]], r[-1]) for r in conn.execute(DRAFTS).fetchall() if r[0] in names]
    assert rows == [("fresh_nine", True), ("fresh_eight", True), ("old_ten", False), ("old_nine", False), ("fresh_seven", False)]


def test_with_the_apply_runner_on_remote_jobs_at_the_apply_floor_are_drafted_too(conn, monkeypatch):
    monkeypatch.setitem(tailor.SETTINGS, "apply", {"enabled": False})  # pin the old rule; settings.yaml may have the apply runner on
    egypt = add_graded_job(conn, "ap-egypt", 6, place="Egypt")
    remote = add_graded_job(conn, "ap-remote", 6, place="Remote")
    gulf = add_graded_job(conn, "ap-gulf", 9, place="UAE")
    mine = {"egypt6": egypt, "remote6": remote, "gulf9": gulf}
    assert picked(conn, mine) == ["gulf9"] or "remote6" not in picked(conn, mine)  # apply off: the old rule (score 8+, ai_places)
    monkeypatch.setitem(tailor.SETTINGS, "apply", {"enabled": True, "score_floor": 6, "places": ["Egypt", "Remote"]})
    got = picked(conn, mine)
    assert "remote6" in got and "egypt6" in got and "gulf9" not in got
