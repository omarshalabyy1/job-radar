"""collect_inbox: the daily run stores the last 14 days of every mailbox in core.mail (job_radar.mail_inbox.collect), so
the tracker's Inbox is never days behind."""

from pathlib import Path

from job_radar import __main__ as main_module
from job_radar import mail_inbox, steps

DAG = Path(__file__).resolve().parent.parent / "dags" / "job_radar.py"


def test_collect_inbox_is_a_step_that_runs_the_inbox_collect(monkeypatch):
    called = []
    monkeypatch.setattr(mail_inbox, "collect", lambda conn: called.append(conn))
    steps.collect_inbox("conn")
    assert called == ["conn"]
    assert "collect_inbox" in main_module.STEPS


def test_the_daily_run_collects_the_inbox_behind_schema():
    """One of the tasks behind schema (the extracts list), so run_failed watches it like the others."""
    source = DAG.read_text(encoding="utf-8")
    inbox = 'extracts.append(step("collect_inbox", execution_timeout=timedelta(seconds=900)))'
    assert inbox in source
    assert source.index(inbox) < source.index('step("schema", retries=2, retry_delay=timedelta(seconds=60)) >> extracts')
