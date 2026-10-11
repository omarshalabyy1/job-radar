r"""Failures the steps must not hide, without the warehouse or the network. Run on the laptop:

    .venv\Scripts\python -m pytest tests/test_steps.py
"""

import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from job_radar import __main__ as main_module, eligibility, steps

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("env, expected", [("somewhere/else", Path("somewhere/else")), ("", ROOT / "output")])
def test_output_is_the_env_folder_or_the_repos_output(env, expected):
    """One output folder for the laptop's runs and Docker's: JOB_RADAR_OUTPUT, else the repo's output/ (set even
    when empty, so a value in .env cannot leak into the check)."""
    result = subprocess.run([sys.executable, "-c", "from job_radar import config; print(config.OUTPUT)"], cwd=ROOT,
                            capture_output=True, text=True, env={**os.environ, "JOB_RADAR_OUTPUT": env})
    assert Path(result.stdout.strip()) == expected, result.stderr[-500:]


@pytest.mark.parametrize("module", ["job_radar.steps", "job_radar.__main__"])
def test_importing_the_steps_does_not_load_the_claude_sdk(module):
    """The SDK is most of the import time (about 25 of 28 s on a loaded laptop); only the steps that call Claude load it."""
    code = f"import sys, {module}; assert 'anthropic' not in sys.modules, 'anthropic was imported'"
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-500:]


@pytest.mark.parametrize("argv, watched", [
    (["schema", "match_skills", "embed"], False), (["transform"], False), (["grade_claude"], True),
    (["embed", "tailor"], True), (["track_applications"], True), (["eligibility"], True), (["all"], True)])
def test_the_claude_sdk_is_wrapped_only_for_a_run_with_a_claude_step(monkeypatch, argv, watched):
    calls = []
    monkeypatch.setattr(sys, "argv", ["job_radar", *argv])
    monkeypatch.setattr(main_module.runlog, "watch_claude", lambda: calls.append("watch"))
    monkeypatch.setattr(main_module.runlog, "run", lambda conn, name, step: None)
    monkeypatch.setattr(main_module, "connect", lambda: __import__("contextlib").nullcontext())
    main_module.main()
    assert bool(calls) is watched


class EmptyWindow:
    """A connection whose raw.job_posting has nothing in the window; it records everything it is asked."""

    def __init__(self):
        self.sql = []

    def cursor(self, **_):
        return self

    def execute(self, sql, *_):
        self.sql.append(sql)
        return self

    def fetchall(self):
        return []


def test_transform_fails_before_deleting_anything_when_the_window_has_no_posting():
    conn = EmptyWindow()
    with pytest.raises(RuntimeError, match="0 raw postings"):
        steps.transform(conn)
    assert len(conn.sql) == 1 and conn.sql[0].lstrip().startswith("SELECT")  # no DELETE, no INSERT: the jobs stay


class Mailbox:
    """A connection for extract_email: the ids already known, and a record of what is written."""

    def __init__(self):
        self.sql, self.inserted = [], []

    def execute(self, sql, *_):
        self.sql.append(sql)
        return [("known-with-jobs",), ("known-empty",)] if sql.lstrip().startswith("SELECT") else None

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def executemany(self, sql, rows):
        self.sql.append(sql)
        self.inserted += [row[0] for row in rows]

    def commit(self):
        pass


def test_an_email_without_a_job_is_remembered_so_it_is_not_downloaded_again(monkeypatch):
    asked = {}

    def mailboxes(seen, empty):
        asked["seen"] = set(seen)
        empty.update({"<newsletter-1>", "<newsletter-2>"})
        return []

    monkeypatch.setattr(steps.sources, "mailboxes", mailboxes)
    monkeypatch.setattr(steps, "extract", lambda conn, *sources: [source() for source in sources])
    conn = Mailbox()
    steps.extract_email(conn)
    assert asked["seen"] == {"known-with-jobs", "known-empty"}  # the raw postings' ids and core.email_seen's, together
    assert sorted(conn.inserted) == ["<newsletter-1>", "<newsletter-2>"]
    assert any("ON CONFLICT DO NOTHING" in sql and "core.email_seen" in sql for sql in conn.sql)  # a rerun adds nothing twice
    assert any("DELETE FROM core.email_seen" in sql for sql in conn.sql)


# ---- against the warehouse: every row is written in a transaction that is rolled back; skipped without it ----
import smtplib  # noqa: E402

import psycopg  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from job_radar import config, db  # noqa: E402

load_dotenv(ROOT / ".env")


@pytest.fixture
def conn():
    try:
        connection = db.connect()
    except (KeyError, psycopg.OperationalError) as e:
        pytest.skip(f"no warehouse: {e!r}"[:120])
    yield connection
    connection.rollback()  # nothing the test wrote stays
    connection.close()


class RolledBack:
    """The connection with commit turned into nothing: the step commits, the fixture then rolls everything back."""

    def __init__(self, conn):
        self.conn = conn

    def __getattr__(self, name):
        return getattr(self.conn, name)

    def commit(self):
        pass


def add_job(conn, key: str, description: str | None = None, place: str = "Egypt", restriction: str | None = None,
            age: int = 0, emailed: bool = False) -> int:
    return conn.execute(
        "INSERT INTO core.job (job_key, title, company, location, place, source, job_url, role_rank, role, target_company,"
        " description, first_seen, restriction, emailed_at) VALUES (%s, 'Data Engineer', 'Acme', 'Cairo', %s, 'test', %s, 1,"
        " 'Data Engineer', false, %s, current_date - %s, %s, CASE WHEN %s THEN now() END) RETURNING job_id",
        (f"test_steps {key}", place, f"https://test.example/{key}", description, age, restriction, emailed)).fetchone()[0]


class Haiku:
    """Claude Haiku's answers without the network: no for a posting that asks for Fictionland citizenship, else unclear."""

    def __init__(self):
        self.asked = []
        self.messages = self

    def parse(self, **kw):
        text = kw["messages"][0]["content"]
        self.asked.append(text.split(" at ")[0])
        answer = "no" if "Fictionland" in text else "unclear"
        return type("Response", (), {"parsed_output": eligibility.Verdict(answer=answer, reason="Fictionland citizens only")})()


def add_posted(conn, key: str, source: str, location: str, place: str, description: str | None,
               restriction: str | None = None) -> int:
    return conn.execute(
        "INSERT INTO core.job (job_key, title, company, location, place, source, job_url, role_rank, role, target_company,"
        " description, first_seen, restriction) VALUES (%s, %s, 'Zyntrafold', %s, %s, %s, %s, 1, 'Data Engineer', false,"
        " %s, current_date, %s) RETURNING job_id",
        (f"test_steps {key}", key, location, place, source, f"https://test.example/{key}", description, restriction)).fetchone()[0]


def test_eligibility_takes_the_boards_word_then_asks_claude_once_and_deletes_nothing(conn, monkeypatch):
    """A board's own who-can-apply field decides alone; a described job outside ai_places that no board labels is asked
    once; only a 'no' sets a restriction; ai_places' jobs are left to grade_claude (an old regex flag is cleared); a job
    with no field and no text waits for one. No row is deleted and nothing reaches core.job_seen."""
    haiku = Haiku()
    monkeypatch.setitem(eligibility.FEATURES, "eligibility", True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(eligibility.claude, "client", lambda: haiku)
    jobs = {name: add_posted(conn, name, *rest) for name, *rest in (
        ("us_board", "dailyremote", "United States (remote)", "USA", None),
        ("world_board", "remotive", "Worldwide (remote)", "Remote", "Must hold Fictionland citizenship"),  # the board decides
        ("citizens_only", "test", "Remote", "Remote", "Must hold Fictionland citizenship"),
        ("silent", "test", "Remote", "Remote", "SQL and Airflow"),
        ("cairo", "test", "Cairo", "Egypt", "Must hold Fictionland citizenship", "an old regex flag"),
        ("no_text", "test", "Remote", "Remote", None))}

    def state():
        rows = {job_id: (restriction, judged) for job_id, restriction, judged in conn.execute(
            "SELECT job_id, restriction, eligibility_at IS NOT NULL FROM core.job WHERE job_id = ANY(%s)", (list(jobs.values()),))}
        return {name: rows.get(job_id, "deleted") for name, job_id in jobs.items()}

    eligibility.eligibility(RolledBack(conn))
    assert state() == {"us_board": ("open to United States only", True), "world_board": (None, True),
                       "citizens_only": ("Fictionland citizens only", True), "silent": (None, True),
                       "cairo": (None, True), "no_text": (None, False)}
    assert sorted(a for a in haiku.asked if a in jobs) == ["citizens_only", "silent"]  # the warehouse's own jobs aside
    assert conn.execute("SELECT count(*) FROM core.job_seen WHERE job_key LIKE 'test_steps %'").fetchone() == (0,)
    eligibility.eligibility(RolledBack(conn))  # each job is judged once
    assert sorted(a for a in haiku.asked if a in jobs) == ["citizens_only", "silent"]  # the warehouse's own jobs aside


def test_eligibility_off_judges_nothing(conn, monkeypatch, capsys):
    monkeypatch.setitem(eligibility.FEATURES, "eligibility", False)
    job = add_posted(conn, "us_board", "dailyremote", "United States (remote)", "USA", None)
    eligibility.eligibility(RolledBack(conn))
    assert conn.execute("SELECT restriction, eligibility_at FROM core.job WHERE job_id = %s", (job,)).fetchone() == (None, None)
    assert "off in settings.yaml (features.eligibility)" in capsys.readouterr().out


def test_the_email_leaves_out_ignored_rejected_and_hidden_jobs(conn, monkeypatch):
    sent = []
    monkeypatch.setattr(steps, "digest", lambda jobs, name, follow_ups=(): sent.append({j["job_id"] for j in jobs}) or "body")
    monkeypatch.setattr(steps, "send", lambda body, title, name: False)  # nothing is sent and, as the email is not sent, nothing is marked emailed
    monkeypatch.setattr(smtplib, "SMTP_SSL", lambda *a, **k: pytest.fail("an email was sent"))  # belt and braces
    for switch in ("auto_hide", "eligibility", "grade"):
        monkeypatch.setitem(config.FEATURES, switch, True)
    jobs = {name: add_job(conn, name, **kw) for name, kw in {
        "plain": {}, "saved": {}, "restricted_but_saved": {"restriction": "US citizens only"}, "ok_score": {},
        "ignored": {}, "rejected": {}, "restricted": {"restriction": "US citizens only"}, "low_score": {}, "claude_bars_it": {},
        "already_emailed": {"emailed": True}, "too_old": {"age": 2}, "abroad": {"place": "Remote"}}.items()}
    for name, status in (("saved", "saved"), ("restricted_but_saved", "saved"), ("ignored", "ignored"), ("rejected", "rejected")):
        conn.execute("INSERT INTO core.application (job_id, status) VALUES (%s, %s)", (jobs[name], status))
    for name, score, restriction in (("ok_score", 4, None), ("low_score", 3, None), ("claude_bars_it", 9, "needs a US work permit")):
        conn.execute("INSERT INTO core.claude_grade (job_id, score, cv, reason, model, restriction) VALUES (%s, %s, 'cv', 'why', 'm', %s)",
                     (jobs[name], score, restriction))

    def emailed() -> set[str]:
        sent.clear()
        steps.email(conn, home=True)
        return {name for name, job_id in jobs.items() if job_id in sent[0]}

    assert emailed() == {"plain", "saved", "restricted_but_saved", "ok_score"}  # your status wins over a hide; a score of 4 is no bar
    monkeypatch.setitem(config.FEATURES, "grade", False)  # scores ignored: a low score no longer hides
    monkeypatch.setitem(config.FEATURES, "eligibility", False)  # restrictions ignored
    assert emailed() == {"plain", "saved", "restricted_but_saved", "ok_score", "restricted", "low_score", "claude_bars_it"}
    monkeypatch.setitem(config.FEATURES, "auto_hide", False)  # ignored and rejected stay out whatever the switches say
    assert emailed() == {"plain", "saved", "restricted_but_saved", "ok_score", "restricted", "low_score", "claude_bars_it"}
    assert conn.execute("SELECT count(*) FROM core.job WHERE job_id = ANY(%s) AND emailed_at IS NOT NULL", (list(jobs.values()),)).fetchone() == (1,)


def test_the_email_hides_a_restriction_by_eligibility_alone_and_a_low_score_by_auto_hide_and_grade(conn, monkeypatch):
    sent = []
    monkeypatch.setattr(steps, "digest", lambda jobs, name, follow_ups=(): sent.append({j["job_id"] for j in jobs}) or "body")
    monkeypatch.setattr(steps, "send", lambda body, title, name: False)
    monkeypatch.setattr(smtplib, "SMTP_SSL", lambda *a, **k: pytest.fail("an email was sent"))
    jobs = {"restricted": add_job(conn, "restricted", restriction="US citizens only"), "low_score": add_job(conn, "low_score")}
    conn.execute("INSERT INTO core.claude_grade (job_id, score, cv, reason, model) VALUES (%s, 2, 'cv', 'why', 'm')", (jobs["low_score"],))

    def emailed(**switches):
        for switch, on in {"eligibility": True, "auto_hide": True, "grade": True, **switches}.items():
            monkeypatch.setitem(config.FEATURES, switch, on)
        sent.clear()
        steps.email(conn, home=True)
        return {name for name, job_id in jobs.items() if sent and job_id in sent[0]}  # no email at all: nothing

    assert emailed() == set()                                     # both hide
    assert emailed(auto_hide=False) == {"low_score"}              # the restriction still hides
    assert emailed(eligibility=False) == {"restricted"}           # the low score still hides
    assert emailed(eligibility=False, auto_hide=False) == {"restricted", "low_score"}


def test_export_career_ops_leaves_out_ignored_and_hidden_jobs(conn, monkeypatch, tmp_path):
    """The best 25 are chosen after the hiding, so a hidden job does not take a place."""
    monkeypatch.setattr(steps, "OUTPUT", tmp_path)
    jobs = {name: add_job(conn, name, restriction=restriction) for name, restriction in
            (("plain", None), ("restricted", "US citizens only"), ("ignored", None), ("low_score", None))}
    conn.execute("INSERT INTO core.application (job_id, status) VALUES (%s, 'ignored')", (jobs["ignored"],))
    conn.execute("INSERT INTO core.claude_grade (job_id, score, cv, reason, model) VALUES (%s, 1, 'cv', 'why', 'm')", (jobs["low_score"],))
    conn.execute("UPDATE core.job SET role_rank = -1, target_company = true WHERE job_id = ANY(%s)", (list(jobs.values()),))  # best first
    skills = [s for (s,) in conn.execute("SELECT skill FROM core.skill ORDER BY skill LIMIT 3").fetchall()]
    for job_id in jobs.values():
        conn.execute("INSERT INTO core.job_skill (job_id, skill) SELECT %s, unnest(%s::text[])", (job_id, skills))

    def exported(**switches):
        for switch, on in {"eligibility": True, "auto_hide": True, "grade": True, **switches}.items():
            monkeypatch.setitem(config.FEATURES, switch, on)
        steps.export_career_ops(conn)
        listed = (tmp_path / "career-ops" / "pipeline.md").read_text(encoding="utf-8")
        return {name for name in jobs if f"https://test.example/{name} |" in listed}

    assert exported() == {"plain"}
    assert exported(eligibility=False) == {"plain", "restricted"}
    assert exported(auto_hide=False) == {"plain", "low_score"}
    assert exported(eligibility=False, auto_hide=False, grade=False) == {"plain", "restricted", "low_score"}  # ignored stays out


def test_the_egypt_email_goes_out_on_a_day_with_no_new_job_when_a_follow_up_is_due(conn, monkeypatch):
    """A follow-up must not wait for a day with a new job. Nothing is sent: send is a fake."""
    from datetime import datetime, time, timedelta

    from job_radar import applications

    sent = []
    monkeypatch.setattr(steps, "send", lambda body, title, name: sent.append((body, title, name)) or False)
    monkeypatch.setattr(smtplib, "SMTP_SSL", lambda *a, **k: pytest.fail("an email was sent"))
    conn.execute("UPDATE core.job SET emailed_at = now() WHERE emailed_at IS NULL")  # no new job today (rolled back)
    conn.execute("INSERT INTO core.application_email (message_id, received_at, sender, subject, company, kind)"
                 " VALUES ('<zz-empty-day>', %s, 'hr@zz.example', 'Thank you for applying', 'Zz Solo', 'applied')",
                 (datetime.combine(applications.cairo_today() - timedelta(days=7), time(10), applications.CAIRO),))
    monkeypatch.setitem(config.FEATURES, "follow_up", True)
    steps.email(conn, home=False)
    assert sent == []                                                    # the other email has no follow-ups: nothing
    steps.email(conn, home=True)
    (body, title, name), = sent
    assert "0 new jobs" in title and "to follow up" in title and "Zz Solo" in body and name == config.HOME
    sent.clear()
    monkeypatch.setitem(config.FEATURES, "follow_up", False)             # switched off: back to nothing
    steps.email(conn, home=True)
    assert sent == []


def test_a_second_collect_the_same_day_does_not_send_the_follow_ups_alone_again(conn, monkeypatch, tmp_path):
    """The follow-up-only Egypt email goes once per Cairo day (a marker in output/); a day with a new job still sends. send is a fake and the commit a no-op."""
    from datetime import datetime, time, timedelta

    from job_radar import applications

    sent = []
    monkeypatch.setattr(steps, "send", lambda body, title, name: sent.append(title) or True)  # pretends Gmail accepted it
    monkeypatch.setattr(smtplib, "SMTP_SSL", lambda *a, **k: pytest.fail("an email was sent"))
    monkeypatch.setattr(steps, "OUTPUT", tmp_path)
    monkeypatch.setitem(config.FEATURES, "follow_up", True)
    conn.execute("UPDATE core.job SET emailed_at = now() WHERE emailed_at IS NULL")  # no new job today (rolled back)
    conn.execute("INSERT INTO core.application_email (message_id, received_at, sender, subject, company, kind)"
                 " VALUES ('<zz-twice>', %s, 'hr@zz.example', 'Thank you for applying', 'Zz Twice', 'applied')",
                 (datetime.combine(applications.cairo_today() - timedelta(days=7), time(10), applications.CAIRO),))
    steps.email(RolledBack(conn), home=True)
    assert len(sent) == 1 and (tmp_path / f"followups-emailed-{applications.cairo_today()}").exists()
    steps.email(RolledBack(conn), home=True)
    assert len(sent) == 1                                   # the second collect: already sent today
    add_job(conn, "second collect, new job")
    steps.email(RolledBack(conn), home=True)
    assert len(sent) == 2 and "to follow up" in sent[1]     # a new job sends, the follow-up riding along


def test_a_step_that_starts_again_marks_its_killed_runs_failed_and_leaves_the_rest(conn):
    """A 'running' row older than the longest Airflow timeout (runlog.STALE_SECONDS) is a run Airflow killed; a younger
    one may be a run in progress, another step's row is not this step's to close, and a finished row stays as it is."""
    from job_radar import runlog

    for age, step, status in [(11, "zz_step", "running"), (1, "zz_step", "running"), (11, "zz_other", "running"), (30, "zz_step", "ok")]:
        conn.execute("INSERT INTO core.step_run (step, started_at, status) VALUES (%s, now() - make_interval(mins => %s), %s)",
                     (step, age, status))
    assert runlog.close_stale(conn, "zz_step") == 1
    rows = conn.execute("SELECT step, (extract(epoch FROM now() - started_at) / 60)::int, status, summary FROM core.step_run"
                        " WHERE step LIKE 'zz%' ORDER BY 1, 2").fetchall()
    assert rows == [("zz_other", 11, "running", None), ("zz_step", 1, "running", None),
                    ("zz_step", 11, "failed", "killed (timeout or Airflow restart)"), ("zz_step", 30, "ok", None)]
    assert runlog.close_stale(conn, "zz_step") == 0  # a second start finds nothing more to close


def test_descendants_are_every_child_and_grandchild_of_the_process_whatever_its_name(tmp_path):
    """From a fake /proc: the name in stat can hold spaces and brackets, and a process of another tree is not ours."""
    for pid, name, ppid in [(10, "python", 1), (20, "node", 10), (30, "(Web Content) x", 20), (40, "bash", 1), (50, "sleep", 40)]:
        (tmp_path / str(pid)).mkdir()
        (tmp_path / str(pid) / "stat").write_text(f"{pid} ({name}) S {ppid} {pid} 0 0 -1 4194560\n")
    (tmp_path / "self").mkdir()  # not a process
    assert main_module.descendants(10, str(tmp_path)) == {20, 30}
    assert main_module.descendants(1, str(tmp_path)) == {10, 20, 30, 40, 50}
    assert main_module.descendants(10, str(tmp_path / "no-proc")) == set()  # Windows: no /proc, nothing to kill


def leaving(step: str, watchdog_seconds: int = 210):
    """`python -m job_radar extract_bayt` with the step replaced by `step` (one line of Python), the warehouse unreachable
    (port 1: the watchdog's run-log write must fail quietly, and no test writes to the real one). Returns the finished
    process and the seconds it took; an output pipe still held open by a child would make it wait for that child."""
    code = "\n".join([
        "import sys, time", "from contextlib import nullcontext", "from job_radar import __main__ as m",
        f"m.WATCHDOG_SECONDS = {watchdog_seconds}", "m.connect = lambda: nullcontext()",
        "m.runlog.run = lambda conn, name, step: step(conn)", f"def step(conn):\n    {step}",
        "m.steps.extract_bayt = step", "sys.argv = ['job_radar', 'extract_bayt']", "m.run()"])
    env = {**os.environ, "WAREHOUSE_HOST": "127.0.0.1", "WAREHOUSE_PORT": "1", "WAREHOUSE_PASSWORD": "x"}
    began = time.monotonic()
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, env=env, timeout=45)
    return result, time.monotonic() - began


def test_a_hung_extract_step_is_stopped_by_its_watchdog_and_ends_green_with_a_warning():
    result, seconds = leaving("time.sleep(60)", watchdog_seconds=1)
    assert result.returncode == 0, result.stderr[-500:]
    assert "WARNING extract_bayt: stopped by its 1 s watchdog; work already saved is kept" in result.stdout
    assert "WARNING run log: extract_bayt not recorded" in result.stdout  # the unreachable warehouse: said, not raised
    assert seconds < 30  # not the step's 60


@pytest.mark.skipif(not os.path.isdir("/proc"), reason="needs /proc: Linux (Docker), not the laptop's Windows")
def test_leaving_kills_a_browser_the_step_left_running_so_the_output_pipe_ends():
    """A child (and its child) that keeps the step's output open: before the fix the run waited for it, 60 s here."""
    sleeper = "import time; time.sleep(60)"
    parent = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {sleeper!r}]); time.sleep(60)"
    result, seconds = leaving(f"import subprocess, sys; subprocess.Popen([sys.executable, '-c', {parent!r}])")
    assert result.returncode == 0, result.stderr[-500:]
    assert seconds < 30


def test_a_watchdog_stop_turns_only_this_runs_running_row_into_warn(conn):
    from job_radar import runlog

    for age, step in [(1, "zz_step"), (10, "zz_step"), (1, "zz_other")]:
        conn.execute("INSERT INTO core.step_run (step, started_at, status) VALUES (%s, now() - make_interval(mins => %s), 'running')",
                     (step, age))
    since = datetime.now(timezone.utc) - timedelta(minutes=5)  # the process began 5 minutes ago
    assert runlog.stop_running(conn, "zz_step", since, "WARNING zz_step: stopped") == 1
    rows = conn.execute("SELECT step, (extract(epoch FROM now() - started_at) / 60)::int, status, summary,"
                        " finished_at IS NOT NULL FROM core.step_run WHERE step LIKE 'zz%' ORDER BY 1, 2").fetchall()
    assert rows == [("zz_other", 1, "running", None, False), ("zz_step", 1, "warn", "WARNING zz_step: stopped", True),
                    ("zz_step", 10, "running", None, False)]
