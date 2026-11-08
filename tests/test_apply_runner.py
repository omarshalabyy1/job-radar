"""job_radar/apply/runner.py: one tick of the apply runner. Fake adapter, fake page, no network, no LLM."""
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import pytest

from job_radar import claude, config, db, llm, tailor
from job_radar.apply import queue, runner
from job_radar.apply.adapters.base import Field, Receipt

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
PREFIX = "apply-runner-test-"
CFG = {"enabled": True, "mode": "dry_run", "score_floor": 6, "max_years": 3, "fresh_hours": 48, "daily_total": 40, "per_board": 20,
       "board_caps": {}, "company_days": 14, "boards": ["greenhouse", "lever"], "places": ["Egypt", "Remote"]}
LIVE = {**CFG, "mode": "live"}


class FakePage:
    def __init__(self):
        self.visited, self.shots, self.closed = [], [], False

    def goto(self, url, **kw):
        self.visited.append(url)

    def screenshot(self, path=None, **kw):
        self.shots.append(path)
        Path(path).write_bytes(b"png")

    def close(self):
        self.closed = True


class FakeAdapter:
    """Scripted per job url: `fields` the fill leaves unanswered, `human` for needs_human, `receipt`, and what raises."""

    def __init__(self, name="greenhouse", human=(), unanswered=None, receipts=None, boom_in=None):
        self.name, self.human, self.unanswered = name, set(human), unanswered or {}
        self.receipts, self.boom_in = receipts or {}, boom_in or {}
        self.calls, self.row_at_submit = [], {}
        self.conn = None

    def can_handle(self, url):
        return queue.board_of(url) == self.name

    def _boom(self, step, page):
        if (exc := self.boom_in.get((step, page.visited[-1]))):
            raise exc

    def needs_human(self, page):
        self.calls.append(("needs_human", page.visited[-1]))
        self._boom("needs_human", page)
        return page.visited[-1] in self.human

    def fill(self, page, job, resolve):
        self.calls.append(("fill", page.visited[-1]))
        self._boom("fill", page)
        resolve("Notice period", "text", None)  # the answer the runner records as sent
        self.job = job
        return [Field(label, "text") for label in self.unanswered.get(page.visited[-1], [])]

    def submit(self, page):
        url = page.visited[-1]
        self.calls.append(("submit", url))
        if self.conn is not None:  # what the warehouse says at the very moment of the click
            self.row_at_submit[url] = self.conn.execute(
                "SELECT outcome FROM core.apply_attempt a JOIN core.job j USING (job_id) WHERE j.job_url = %s", (url,)).fetchone()
        self._boom("submit", page)
        return self.receipts.get(url, Receipt(True, "Thanks for applying"))

    def submitted(self):
        return [url for step, url in self.calls if step == "submit"]


@pytest.fixture
def conn():
    try:
        connection = db.connect()
    except (KeyError, psycopg.OperationalError) as e:
        pytest.skip(f"no warehouse: {e!r}"[:120])
    yield connection
    connection.rollback()
    # the runner commits (an attempt row must outlive a crash), so this test's rows are deleted by hand; cascade takes the rest
    connection.execute("DELETE FROM core.job WHERE job_key LIKE %s", (PREFIX + "%",))
    connection.commit()
    connection.close()


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    """Drafts, screenshots and the kill-switch file live in tmp_path; the closed-page check and the PDF check say all is well."""
    monkeypatch.setattr(config, "OUTPUT", tmp_path)
    monkeypatch.setattr(tailor, "OUTPUT", tmp_path)
    monkeypatch.setattr(runner, "OUTPUT", tmp_path)
    monkeypatch.setattr(queue, "STOP_FILE", tmp_path / "apply.stop")
    monkeypatch.setattr(runner, "closed_now", lambda url: None)
    monkeypatch.setattr(runner, "pdf_problems", lambda pdf, page_html, pages=1: [])
    monkeypatch.setattr(runner.answers, "load", lambda path=None: [{"match": ["notice period"], "answer": "1 month"}])
    return tmp_path


@pytest.fixture
def pages():
    made = []

    def factory(job):
        made.append(FakePage())
        return made[-1]

    factory.made = made
    return factory


def make_job(conn, n, company=None, url=None, score=9, title="Data Engineer", draft=True):
    url = url or f"https://boards.greenhouse.io/co{n}/jobs/{n}"
    company = company or f"Company{n}"
    job_id = conn.execute(
        "INSERT INTO core.job (job_key, title, company, location, place, source, job_url, role_rank, role, target_company, first_seen,"
        " posted_at, apply_url) VALUES (%s, %s, %s, 'Cairo', 'Egypt', 'test', %s, 1, 'Data Engineer', false, %s, %s, %s) RETURNING job_id",
        (f"{PREFIX}{n}", title, company, url, NOW.date(), NOW, url)).fetchone()[0]
    conn.execute("INSERT INTO core.claude_grade (job_id, score, cv, reason, model) VALUES (%s, %s, 'cv', 'ok', 'm')", (job_id, score))
    conn.commit()
    if draft:
        write_draft(config.OUTPUT, company, title, job_id)
    return job_id, url


def write_draft(out, company, title, job_id):
    folder = tailor.draft_folder("2026-11-08", company, title, job_id)
    folder.mkdir(parents=True)
    (folder / f"Omar {company} CV.pdf").write_bytes(b"%PDF cv")
    (folder / f"Omar {company} CV Cover Letter.pdf").write_bytes(b"%PDF letter")
    (folder / "cv.html").write_text("<h2>Experience</h2>")
    return folder


def attempts(conn, job_id):
    return conn.execute("SELECT outcome, board, answers, screenshot, confirmation FROM core.apply_attempt WHERE job_id = %s ORDER BY id",
                        (job_id,)).fetchall()


def outcomes(conn):
    return {job_id: [a[0] for a in attempts(conn, job_id)] for job_id in
            [r[0] for r in conn.execute("SELECT job_id FROM core.job WHERE job_key LIKE %s", (PREFIX + "%",))]}


def tick(conn, adapter, pages, cfg=CFG):
    adapter.conn = conn
    return runner.tick(conn, [adapter], NOW, cfg, page_factory=pages)


# --- nothing to do

def test_an_empty_queue_does_nothing_and_makes_no_llm_call(conn, pages, monkeypatch):
    def no_llm(*a, **k):
        raise AssertionError("the runner called an LLM")

    monkeypatch.setattr(claude, "client", no_llm)
    monkeypatch.setattr(llm, "ask_json", no_llm)
    adapter = FakeAdapter()
    result = tick(conn, adapter, pages)  # no job in the warehouse
    assert result == runner.TickResult()
    assert (result.submitted, result.parked, result.unconfirmed, result.skipped, result.dry_run) == (0, 0, 0, 0, 0)
    assert adapter.calls == [] and pages.made == []


def test_a_disabled_runner_picks_nothing(conn, pages):
    job_id, _ = make_job(conn, 1)
    assert tick(conn, FakeAdapter(), pages, {**CFG, "enabled": False}) == runner.TickResult()
    assert attempts(conn, job_id) == []


# --- dry run (the default)

def test_dry_run_fills_screenshots_and_never_clicks_submit(conn, pages, sandbox):
    job_id, url = make_job(conn, 1)
    adapter = FakeAdapter()
    result = tick(conn, adapter, pages)
    assert result.dry_run == 1 and result.submitted == 0 and not result.breaker_tripped
    assert adapter.submitted() == []
    outcome, board, answers, shot, _ = attempts(conn, job_id)[0]
    assert (outcome, board) == ("dry_run", "greenhouse")
    assert answers == {"Notice period": "1 month"}  # the exact answers the form got
    assert shot == str(sandbox / "apply" / "2026-11-08" / f"{job_id}.png") and Path(shot).read_bytes() == b"png"
    assert pages.made[0].visited == [url] and pages.made[0].closed


def test_the_mode_defaults_to_dry_run_and_anything_but_live_is_dry_run(conn, pages):
    job_id, _ = make_job(conn, 1)
    adapter = FakeAdapter()
    assert tick(conn, adapter, pages, {k: v for k, v in CFG.items() if k != "mode"}).dry_run == 1
    assert adapter.submitted() == [] and outcomes(conn)[job_id] == ["dry_run"]


def test_the_tailored_pdfs_reach_the_adapter(conn, pages):
    job_id, _ = make_job(conn, 1, company="Acme")
    adapter = FakeAdapter()
    tick(conn, adapter, pages)
    assert adapter.job.cv_pdf.name == "Omar Acme CV.pdf" and adapter.job.cover_pdf.name == "Omar Acme CV Cover Letter.pdf"
    assert adapter.job.job_id == job_id and adapter.job.title == "Data Engineer"


# --- live: mark first, click second

def test_live_marks_the_attempt_unconfirmed_before_the_click_then_submitted(conn, pages):
    job_id, url = make_job(conn, 1)
    adapter = FakeAdapter()
    result = tick(conn, adapter, pages, LIVE)
    assert adapter.row_at_submit[url] == ("unconfirmed",)  # the row existed, as unconfirmed, when submit ran
    assert result.submitted == 1 and not result.breaker_tripped
    rows = attempts(conn, job_id)
    assert [r[0] for r in rows] == ["submitted"] and rows[0][4] == "Thanks for applying"  # one row, updated in place


def test_no_confirmation_signal_is_unconfirmed_and_trips_the_breaker(conn, pages):
    (a, ua), (b, ub), (c, uc) = [make_job(conn, n) for n in (1, 2, 3)]
    adapter = FakeAdapter(receipts={ua: Receipt(False, "no message")})
    result = tick(conn, adapter, pages, LIVE)
    assert result.unconfirmed == 1 and result.breaker_tripped
    assert adapter.submitted() == [ua]  # the first unconfirmed flips the rest of the tick to dry run
    assert outcomes(conn) == {a: ["unconfirmed"], b: ["dry_run"], c: ["dry_run"]}
    assert result.dry_run == 2 and result.submitted == 0


def test_a_crash_after_the_click_never_submits_twice(conn, pages):
    (a, ua), (b, ub) = make_job(conn, 1), make_job(conn, 2)
    adapter = FakeAdapter(boom_in={("submit", ua): TimeoutError("page died after the click")})
    result = tick(conn, adapter, pages, LIVE)
    assert outcomes(conn)[a] == ["unconfirmed"]  # not 'failed': the click may have gone out, so it counts and is never retried
    assert "page died" in attempts(conn, a)[0][4]
    assert result.unconfirmed == 1 and result.breaker_tripped and adapter.submitted() == [ua]
    # the next tick: both jobs have a row now (a: unconfirmed, b: dry_run), so nothing is picked and nothing is clicked again
    assert tick(conn, adapter, pages, LIVE) == runner.TickResult()
    assert adapter.submitted() == [ua]


# --- skips

def test_a_closed_page_is_skipped_not_submitted(conn, pages, monkeypatch):
    job_id, url = make_job(conn, 1)
    monkeypatch.setattr(runner, "closed_now", lambda u: "page gone")
    adapter = FakeAdapter()
    result = tick(conn, adapter, pages, LIVE)
    assert result.skipped == 1 and adapter.calls == []
    outcome, _, _, _, why = attempts(conn, job_id)[0]
    assert outcome == "skipped" and "page gone" in why  # every skip writes its reason


def test_a_job_with_no_draft_is_skipped(conn, pages):
    job_id, _ = make_job(conn, 1, draft=False)
    adapter = FakeAdapter()
    result = tick(conn, adapter, pages, LIVE)
    assert result.skipped == 1 and adapter.calls == [] and pages.made == []
    assert "no draft" in attempts(conn, job_id)[0][4]


def test_a_draft_whose_pdf_has_problems_is_skipped(conn, pages, monkeypatch):
    job_id, _ = make_job(conn, 1)
    monkeypatch.setattr(runner, "pdf_problems", lambda pdf, page_html, pages=1: ["is 2 pages, not 1"])
    adapter = FakeAdapter()
    result = tick(conn, adapter, pages, LIVE)
    assert result.skipped == 1 and adapter.calls == []
    assert "is 2 pages, not 1" in attempts(conn, job_id)[0][4]


def test_a_job_no_adapter_handles_is_skipped(conn, pages):
    job_id, _ = make_job(conn, 1)
    result = tick(conn, FakeAdapter(name="lever"), pages, LIVE)
    assert result.skipped == 1 and outcomes(conn)[job_id] == ["skipped"]
    assert "no adapter" in attempts(conn, job_id)[0][4]


def test_the_newest_draft_is_used_when_there_are_two_days(conn, pages, sandbox):
    job_id, _ = make_job(conn, 1, company="Acme")
    newer = tailor.draft_folder("2026-11-09", "Acme", "Data Engineer", job_id)
    newer.mkdir(parents=True)
    (newer / "Omar Acme CV.pdf").write_bytes(b"new")
    (newer / "Omar Acme CV Cover Letter.pdf").write_bytes(b"new")
    (newer / "cv.html").write_text("x")
    adapter = FakeAdapter()
    tick(conn, adapter, pages)
    assert adapter.job.cv_pdf.parent == newer


# --- parking

def test_unanswered_required_fields_park_the_job_and_do_not_submit(conn, pages):
    job_id, url = make_job(conn, 1)
    adapter = FakeAdapter(unanswered={url: ["Expected salary", "Visa status"]})
    result = tick(conn, adapter, pages, LIVE)
    assert result.parked == 1 and adapter.submitted() == [] and outcomes(conn)[job_id] == ["parked"]
    questions = {q for (q,) in conn.execute("SELECT question FROM core.apply_question WHERE job_id = %s", (job_id,))}
    assert questions == {"Expected salary", "Visa status"}


def test_needs_human_parks_and_stops_that_board_for_the_tick(conn, pages):
    (a, ua), (b, ub) = make_job(conn, 1), make_job(conn, 2)
    lever_job, lever_url = make_job(conn, 3, url="https://jobs.lever.co/co3/3")
    adapters = [FakeAdapter(human={ua}), FakeAdapter(name="lever")]
    result = runner.tick(conn, adapters, NOW, LIVE, page_factory=pages)
    assert outcomes(conn)[a] == ["parked"] and outcomes(conn)[b] == []  # b's board is stopped: untouched, picked next tick
    assert outcomes(conn)[lever_job] == ["submitted"]  # another board carries on
    assert adapters[0].submitted() == [] and result.parked == 1 and result.submitted == 1
    assert not result.breaker_tripped


def test_three_parked_in_a_row_flip_the_rest_to_dry_run(conn, pages):
    jobs = [make_job(conn, n, url=f"https://jobs.lever.co/co{n}/{n}") for n in range(1, 6)]
    adapter = FakeAdapter(name="lever", unanswered={u: ["Visa status"] for _, u in jobs[:3]})
    result = tick(conn, adapter, pages, {**LIVE, "boards": ["lever"]})
    assert result.parked == 3 and result.breaker_tripped
    assert [outcomes(conn)[j] for j, _ in jobs] == [["parked"]] * 3 + [["dry_run"]] * 2
    assert adapter.submitted() == [] and result.dry_run == 2


def test_two_parks_then_a_success_do_not_trip_the_breaker(conn, pages):
    jobs = [make_job(conn, n, url=f"https://jobs.lever.co/co{n}/{n}") for n in range(1, 5)]
    adapter = FakeAdapter(name="lever", unanswered={jobs[0][1]: ["Q"], jobs[1][1]: ["Q"], jobs[3][1]: ["Q"]})
    result = tick(conn, adapter, pages, {**LIVE, "boards": ["lever"]})
    assert (result.parked, result.submitted, result.breaker_tripped) == (3, 1, False)


# --- crashes

def test_a_crash_in_the_adapter_records_failed_and_the_next_job_goes_on(conn, pages):
    (a, ua), (b, ub) = make_job(conn, 1), make_job(conn, 2)
    adapter = FakeAdapter(boom_in={("fill", ua): RuntimeError("selector gone")})
    result = tick(conn, adapter, pages, LIVE)
    assert outcomes(conn) == {a: ["failed"], b: ["submitted"]}
    assert "selector gone" in attempts(conn, a)[0][4]
    assert result.submitted == 1 and result.failed == 1 and adapter.submitted() == [ub]
    assert pages.made[0].closed  # the page is closed even when the adapter raised


def test_a_page_factory_that_raises_records_failed(conn):
    job_id, _ = make_job(conn, 1)

    def broken(job):
        raise OSError("no browser")

    result = runner.tick(conn, [FakeAdapter()], NOW, LIVE, page_factory=broken)
    assert result.failed == 1 and outcomes(conn)[job_id] == ["failed"]


def test_a_failed_job_is_not_picked_again_in_the_next_tick(conn, pages):
    job_id, url = make_job(conn, 1)
    adapter = FakeAdapter(boom_in={("fill", url): RuntimeError("x")})
    tick(conn, adapter, pages, LIVE)
    adapter.boom_in = {}
    assert tick(conn, adapter, pages, LIVE) == runner.TickResult()
    assert outcomes(conn)[job_id] == ["failed"]


# --- the kill switch holds mid-queue

def test_the_kill_switch_file_stops_the_tick_mid_queue(conn, pages, sandbox):
    (a, ua), (b, ub) = make_job(conn, 1), make_job(conn, 2)
    adapter = FakeAdapter()
    original = adapter.submit

    def submit_then_stop(page):
        receipt = original(page)
        (sandbox / "apply.stop").write_text("stop")
        return receipt

    adapter.submit = submit_then_stop
    result = tick(conn, adapter, pages, LIVE)
    assert result.submitted == 1 and outcomes(conn) == {a: ["submitted"], b: []}


# --- the default page factory

def test_the_default_page_factory_opens_a_real_page_and_closes_it(tmp_path):
    factory = runner.PlaywrightPages()
    try:
        try:
            page = factory(None)
        except Exception as e:  # no Chromium on this machine
            pytest.skip(f"no browser: {e!r}"[:120])
        page.goto("data:text/html,<h1>hi</h1>")
        page.screenshot(path=str(tmp_path / "x.png"))
        assert (tmp_path / "x.png").stat().st_size > 0
        page.close()
    finally:
        factory.close()
