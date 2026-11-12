r"""The outreach DAGs (dags/job_radar_outreach.py) and the two steps they run, without Airflow: the DAG file imports Airflow, so it is
run here against stand-ins that record what it builds. Run on the laptop:
.venv\Scripts\python -m pytest tests/test_dag_outreach.py
"""

import importlib.util
import sys
import types
from pathlib import Path

import psycopg
import pytest
import yaml

from job_radar import __main__ as main_module, db, outreach_send, runlog, steps

ROOT = Path(__file__).resolve().parent.parent
DAG_FILE = ROOT / "dags" / "job_radar_outreach.py"
SETTINGS = yaml.safe_load((ROOT / "settings.yaml").read_text(encoding="utf-8"))


def load_dag(monkeypatch, tmp_path, enabled, mode="dry_run"):
    """Run the DAG file with stand-ins for Airflow and a settings.yaml whose outreach block is as given. Returns (dags, operators):
    {dag_id: kwargs} and [(dag_id, task_id, kwargs)]."""
    (tmp_path / "settings.yaml").write_text(yaml.safe_dump({**SETTINGS, "outreach": {**SETTINGS["outreach"], "enabled": enabled,
                                                                                    "mode": mode}}), encoding="utf-8")
    dags, ops, current = {}, [], []

    class FakeDAG:
        def __init__(self, dag_id, **kwargs):
            self.dag_id = dag_id
            dags[dag_id] = kwargs

        def __enter__(self):
            current.append(self.dag_id)
            return self

        def __exit__(self, *a):
            current.pop()

    class FakeBash:
        def __init__(self, task_id, **kwargs):
            ops.append((current[-1], task_id, kwargs))

    fakes = {"airflow": types.ModuleType("airflow"), "airflow.providers": types.ModuleType("airflow.providers"),
             "airflow.providers.standard": types.ModuleType("airflow.providers.standard"),
             "airflow.providers.standard.operators": types.ModuleType("airflow.providers.standard.operators"),
             "airflow.providers.standard.operators.bash": types.ModuleType("airflow.providers.standard.operators.bash"),
             "airflow.sdk": types.ModuleType("airflow.sdk"), "pendulum": types.ModuleType("pendulum")}
    fakes["airflow.providers.standard.operators.bash"].BashOperator = FakeBash
    fakes["airflow.sdk"].DAG = FakeDAG
    fakes["pendulum"].datetime = lambda *a, **k: ("start", a, k)
    for name, module in fakes.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setenv("JOB_RADAR_ROOT", str(tmp_path))
    spec = importlib.util.spec_from_file_location("outreach_dag_under_test", DAG_FILE)
    spec.loader.exec_module(importlib.util.module_from_spec(spec))
    return dags, ops


def test_two_dags_run_the_sender_every_5_minutes_and_the_reply_reader_hourly(monkeypatch, tmp_path):
    dags, ops = load_dag(monkeypatch, tmp_path, enabled=True)
    assert set(dags) == {"job_radar_outreach", "job_radar_outreach_replies"}
    assert dags["job_radar_outreach"]["schedule"] == "*/5 * * * *"
    assert dags["job_radar_outreach_replies"]["schedule"].endswith("* * * *") and dags["job_radar_outreach_replies"]["schedule"].split()[0].isdigit()
    assert [(d, t) for d, t, _ in ops] == [("job_radar_outreach", "outreach_send"), ("job_radar_outreach_replies", "outreach_replies")]


def test_ticks_never_overlap_pile_up_or_retry(monkeypatch, tmp_path):
    """A run still going when the next is due, or a tick missed while the laptop slept, must not become a burst of emails: one run at
    a time, no catch-up, and a failed send is never retried (the next tick decides again)."""
    dags, ops = load_dag(monkeypatch, tmp_path, enabled=True)
    for kwargs in dags.values():
        assert kwargs["max_active_runs"] == 1 and kwargs["catchup"] is False and kwargs["is_paused_upon_creation"] is False
        assert kwargs["default_args"]["retries"] == 0 and kwargs["default_args"]["on_failure_callback"]
    for _, _, kwargs in ops:
        assert "outlets" not in kwargs and kwargs.get("retries", 0) == 0 and kwargs.get("trigger_rule") is None


def test_the_tasks_run_the_two_steps_in_the_job_radar_environment(monkeypatch, tmp_path):
    _, ops = load_dag(monkeypatch, tmp_path, enabled=True)
    commands = {t: k["bash_command"] for _, t, k in ops}
    assert "/opt/job-radar-venv/bin/python -m job_radar outreach_send" in commands["outreach_send"]
    assert "/opt/job-radar-venv/bin/python -m job_radar outreach_replies" in commands["outreach_replies"]
    assert all(k["cwd"] == "/opt/job-radar" for _, _, k in ops)


def test_the_sender_task_skips_when_the_stop_file_is_there_without_starting_python(monkeypatch, tmp_path):
    _, ops = load_dag(monkeypatch, tmp_path, enabled=True)
    command = {t: k["bash_command"] for _, t, k in ops}["outreach_send"]
    guard, run = command.split("/opt/job-radar-venv/bin/python")[0], command
    assert "/opt/job-radar/output/outreach.stop" in guard and "exit 99" in guard      # exit 99 is BashOperator's skip code
    assert command.index("exit 99") < command.index("-m job_radar")


def test_the_reply_reader_is_not_stopped_by_the_stop_file(monkeypatch, tmp_path):
    """An unsubscribe that arrives while sending is stopped must still be honoured."""
    _, ops = load_dag(monkeypatch, tmp_path, enabled=True)
    assert "outreach.stop" not in {t: k["bash_command"] for _, t, k in ops}["outreach_replies"]


def test_with_outreach_off_both_tasks_skip_at_once_and_never_start_python(monkeypatch, tmp_path):
    _, ops = load_dag(monkeypatch, tmp_path, enabled=False)
    for _, _, kwargs in ops:
        command = kwargs["bash_command"]
        assert "exit 99" in command and "python" not in command and "job_radar" not in command.replace("outreach.enabled", "")


@pytest.mark.parametrize("mode", ["dry_run", "live"])
def test_the_dag_does_not_pick_the_mode_the_settings_do(monkeypatch, tmp_path, mode):
    """The DAG only runs the step; whether mail leaves is settings.yaml's outreach.mode AND outreach.enabled, checked in the sender."""
    _, ops = load_dag(monkeypatch, tmp_path, enabled=True, mode=mode)
    assert all("live" not in k["bash_command"] and "dry_run" not in k["bash_command"] and "--" not in k["bash_command"] for _, _, k in ops)


def test_the_shipped_settings_leave_the_dags_idle():
    assert SETTINGS["outreach"]["mode"] == "dry_run"  # enabled is true since 2026-11-10 (Omar: dry run on); only live sends


# ---------------------------------------------------------------------------------------------------------------- the steps

def test_the_steps_are_registered_and_the_module_runs_them_by_name():
    assert "outreach_send" in main_module.STEPS and "outreach_replies" in main_module.STEPS
    assert callable(steps.outreach_send) and callable(steps.outreach_replies)
    for name in ("outreach_send", "outreach_replies"):
        assert name in main_module.__doc__


@pytest.fixture
def conn():
    try:
        connection = db.connect()
    except (KeyError, psycopg.OperationalError) as e:
        pytest.skip(f"no warehouse: {e!r}"[:120])
    connection.execute("DELETE FROM core.step_run WHERE step LIKE 'outreach\\_%'")
    connection.commit()
    yield connection
    connection.rollback()
    connection.execute("DELETE FROM core.step_run WHERE step LIKE 'outreach\\_%'")
    connection.commit()
    connection.close()


@pytest.mark.parametrize("name", ["outreach_send", "outreach_replies"])
def test_a_step_with_outreach_off_is_logged_off_and_touches_nothing(conn, monkeypatch, name):
    monkeypatch.setitem(outreach_send.SETTINGS, "outreach", {**SETTINGS["outreach"], "enabled": False})
    monkeypatch.setattr(outreach_send.smtplib, "SMTP_SSL", lambda *a, **k: pytest.fail("SMTP"))
    monkeypatch.setattr(outreach_send.imaplib, "IMAP4_SSL", lambda *a, **k: pytest.fail("IMAP"))
    runlog.run(conn, name, getattr(steps, name))
    assert conn.execute("SELECT status FROM core.step_run WHERE step = %s", (name,)).fetchone() == ("off",)


def test_the_send_step_runs_one_tick_at_the_current_time(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(steps.outreach_sender, "run", lambda c, now, cfg=None: calls.append((c, now, cfg)) or [])
    steps.outreach_send(conn)
    [(c, now, cfg)] = calls
    assert c is conn and now.tzinfo is not None and cfg is None          # settings.yaml decides; the step overrides nothing


def test_the_replies_step_collects_at_the_current_time(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(steps.outreach_sender, "collect_replies", lambda c, now, cfg=None: calls.append((c, now, cfg)))
    steps.outreach_replies(conn)
    [(c, now, cfg)] = calls
    assert c is conn and now.tzinfo is not None and cfg is None
