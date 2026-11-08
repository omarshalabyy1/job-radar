"""No silent failures: a failed task emails Omar (alert.py), a collect that published with steps failing says so
(steps.run_report), and the day's warnings are in the Egypt email ("Problems in this run"). The sender is mocked."""

import ast
import json
from pathlib import Path

import psycopg
import pytest

from job_radar import alert, steps
from test_steps import RolledBack, conn  # noqa: F401  (the warehouse fixture, rolled back)

DAG = Path(__file__).resolve().parent.parent / "dags" / "job_radar.py"


def test_a_task_that_fails_sends_one_alert_with_the_end_of_its_log(tmp_path, monkeypatch):
    log = tmp_path / "dag_id=job_radar" / "run_id=scheduled__1" / "task_id=extract_companies" / "attempt=3.log"
    log.parent.mkdir(parents=True)
    log.write_text("\n".join(json.dumps({"event": e}) for e in [
        "== extract_companies", "Traceback (most recent call last):",
        "psycopg.OperationalError: consuming input failed: server closed the connection unexpectedly"]), encoding="utf-8")
    sent = []
    monkeypatch.setattr(alert, "LOGS", tmp_path)
    monkeypatch.setattr("job_radar.config.OUTPUT", tmp_path)
    monkeypatch.setattr(alert, "send", lambda subject, lines: sent.append((subject, lines)) or True)
    assert alert.main(["task", "job_radar", "scheduled__1", "extract_companies", "3"]) == 0
    assert alert.main(["task", "job_radar", "scheduled__2", "extract_companies", "1"]) == 0  # the same task again today: no second email
    [(subject, lines)] = sent
    assert subject == "job-radar FAILED: extract_companies"
    assert lines[-1].startswith("psycopg.OperationalError") and "try 3" in lines[0]


def test_an_alert_that_cannot_be_sent_is_said_not_swallowed(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(alert, "LOGS", tmp_path)
    monkeypatch.setattr("job_radar.config.OUTPUT", tmp_path)
    monkeypatch.setattr(alert, "send", lambda subject, lines: (_ for _ in ()).throw(OSError("smtp down")))
    pushed = []
    monkeypatch.setattr("job_radar.digest.push", lambda title, message, url=None: pushed.append(title) or True)
    assert alert.main(["task", "job_radar", "r", "transform", "1"]) == 1
    assert "alert: not sent" in capsys.readouterr().out
    assert pushed == ["job-radar FAILED: transform"]  # the email failed: the phone push says it


def test_a_run_log_that_cannot_be_written_is_alerted(monkeypatch):
    from job_radar import runlog
    sent = []
    monkeypatch.setattr(runlog, "write", lambda row: (_ for _ in ()).throw(psycopg.OperationalError("recovery mode")))
    monkeypatch.setattr(runlog, "SAVE_SECONDS", 0)
    monkeypatch.setattr(alert, "send", lambda subject, lines: sent.append(subject) or True)
    runlog.save({"step": "extract_companies", "status": "failed"})
    assert sent == ["job-radar: run log not recorded for extract_companies"]


def test_every_dag_alerts_on_failure():
    """Each DAG's default_args carry the callback, so a task added later alerts too."""
    tree = ast.parse(DAG.read_text(encoding="utf-8"))
    dags = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "DAG"]
    assert dags and all(any(k.arg == "default_args" for k in d.keywords) for d in dags)
    source = DAG.read_text(encoding="utf-8")
    assert '"on_failure_callback": alert' in source and source.count("default_args=DEFAULTS") + source.count("**DEFAULTS") == len(dags)
    assert 'published >> step("run_report", trigger_rule="all_done")' in source


def test_a_run_that_published_with_a_failed_step_sends_one_alert_saying_so(conn, monkeypatch):  # noqa: F811
    from job_radar import alert as alerts
    sent = []
    monkeypatch.setattr(alerts, "send", lambda subject, lines: sent.append((subject, lines)) or True)
    rows = [("schema", "ok", ""), ("extract_companies", "failed", "Traceback ...\npsycopg.OperationalError: server closed"),
            ("extract_market", "warn", "WARNING extract_market ashby:x: 404\nextract_market: 19 of 20 boards read"),
            ("publish", "ok", "publish: 188 jobs read")]
    for n, (step, status, summary) in enumerate(rows):
        conn.execute("INSERT INTO core.step_run (step, started_at, finished_at, status, summary) VALUES"
                     " (%s, now() + make_interval(secs => %s), now() + make_interval(secs => %s), %s, %s)",
                     (step, 3600 + n, 3600 + n, status, summary))
    steps.run_report(RolledBack(conn))
    [(subject, lines)] = sent
    assert subject == "job-radar: published with 1 step failing"
    assert lines == ["extract_companies (failed): psycopg.OperationalError: server closed"]
    # the warning is not an alert, it is in the Egypt email
    assert [p[0] for p in steps.problems(conn)] == ["extract_companies", "extract_market"]


def test_the_egypt_email_lists_the_problems_and_goes_out_for_them_alone(conn, monkeypatch):  # noqa: F811
    sent = []
    monkeypatch.setattr(steps, "send", lambda body, title, name: sent.append((body, title)) or False)
    monkeypatch.setattr(steps, "problems", lambda conn: [("extract_linkedin", "warn", "WARNING linkedin: 403 over the limit")])
    monkeypatch.setattr(steps, "due_now", lambda conn, *a: [])
    monkeypatch.setattr(steps, "EMAIL_JOBS", "SELECT * FROM mart.job_status WHERE false AND %s::int IS NOT NULL"
                                             " AND %s::text IS NOT NULL AND %s::bool IS NOT NULL AND %s::bool IS NOT NULL")
    steps.email(RolledBack(conn), home=True)
    [(body, title)] = sent
    assert "Problems in this run" in body and "linkedin: 403 over the limit" in body and title.endswith("· 1 problem")


def test_a_step_whose_connection_dies_is_recorded_failed_even_while_the_warehouse_restarts(monkeypatch):
    """2026-10-17: the warehouse reset under extract_companies; its end could not be written ('the database system is in
    recovery mode') and its row stayed 'running'. Now the step's connection is killed mid-step, the run log's first two
    tries to write the end fail as during a restart, and the row still ends 'failed' with the error."""
    from job_radar import db, runlog
    real = runlog.connect
    tries = {"n": 0}

    def restarting():
        tries["n"] += 1
        if tries["n"] in (2, 3):  # the 'running' row is written (try 1); the end's first two tries meet a restart
            raise psycopg.OperationalError("FATAL: the database system is in recovery mode")
        return real()
    monkeypatch.setattr(runlog, "connect", restarting)
    monkeypatch.setattr(runlog, "close_stale", lambda log, step: [])
    monkeypatch.setattr(runlog, "SAVE_PAUSE", 0.01)

    def dies(conn):
        conn.execute("SELECT pg_terminate_backend(pg_backend_pid())")  # the step's own session is killed
    try:
        step_conn = db.connect()
    except (KeyError, psycopg.OperationalError) as e:
        pytest.skip(f"no warehouse: {e!r}"[:120])
    name = "zz_dies_mid_step"
    try:
        with pytest.raises(psycopg.OperationalError):
            runlog.run(step_conn, name, dies)
        with db.connect() as check:
            status, summary = check.execute("SELECT status, summary FROM core.step_run WHERE step = %s", (name,)).fetchone()
            check.execute("DELETE FROM core.step_run WHERE step = %s", (name,))
        assert status == "failed" and ("OperationalError" in summary or "AdminShutdown" in summary) and tries["n"] >= 4  # more when real connects also time out
    finally:
        step_conn.close()


def test_the_collect_marks_steps_an_earlier_run_left_running_and_alerts(conn, monkeypatch):  # noqa: F811
    from job_radar import alert as alerts, runlog
    sent = []
    monkeypatch.setattr(alerts, "send", lambda subject, lines: sent.append((subject, lines)) or True)
    conn.execute("INSERT INTO core.step_run (step, started_at, status) VALUES ('zz_left_running', now() - interval '3 hours', 'running')")
    monkeypatch.setattr(runlog, "connect", lambda: __import__("contextlib").nullcontext(conn))
    monkeypatch.setattr(runlog, "save", lambda row: None)
    runlog.run(conn, "schema", lambda c: None)
    assert conn.execute("SELECT status FROM core.step_run WHERE step = 'zz_left_running'").fetchone() == ("failed",)
    assert sent and "never ended" in sent[0][0] and any("zz_left_running" in line for line in sent[0][1])


def test_many_sources_silent_in_one_run_is_an_alert_and_held_says_refusals(conn, monkeypatch, capsys):  # noqa: F811
    from job_radar import alert as alerts
    from job_radar.sources import base
    sent = []
    monkeypatch.setattr(alerts, "send", lambda subject, lines: sent.append((subject, lines)) or True)
    rows = [("extract_remote", "warn", "WARNING remoteok: 0 postings today, 40 in the last 7 days - has the board changed, or does it block us?"
             " | WARNING himalayas: himalayas.app answered 429: held for 1.0 hours | extract done"),
            ("extract_egypt", "warn", "WARNING wuzzuf: its search API returned no jobs - has the site changed?")]
    monkeypatch.setattr(steps, "problems", lambda c: rows)
    steps.run_report(RolledBack(conn))
    assert sent and sent[0][0] == "job-radar: 3 sources silent or refusing in this run"
    base.held("x", Exception("x.com answered 403: held for 6 hours"))
    base.held("x", Exception("x.com is held for 3 hours more"))
    assert capsys.readouterr().out.splitlines()[-2:] == ["WARNING x: x.com answered 403: held for 6 hours", "x: x.com is held for 3 hours more"]
