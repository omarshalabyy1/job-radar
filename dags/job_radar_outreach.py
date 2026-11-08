"""job_radar_outreach: the outreach sender (job_radar/outreach_send.py) on a schedule, in two DAGs.

    job_radar_outreach          every 5 minutes: outreach_send, one tick = at most one cold email (2 to 10 minutes apart, so the
                                tick must be shorter than the gap)
    job_radar_outreach_replies  hourly: outreach_replies reads the mailboxes read-only for replies and bounces to those emails and
                                suppresses their addresses (core.suppression), pausing a mailbox that bounces or is complained of

Nothing here decides whether mail leaves the laptop. That is settings.yaml (outreach.mode live AND outreach.enabled true), checked again
inside the sender; as shipped it is enabled false, mode dry_run, and both DAGs are then free: each task is a skip (exit 99,
BashOperator's skip code) that never starts Python. In dry run the sender writes output/outreach/outbox/*.eml and rows of
core.outreach_send with mode dry_run, and sends nothing. The file output/outreach.stop skips the sender's tick; the reply reader
ignores it, because an unsubscribe that arrives while sending is stopped must still be honoured.

A tick is never retried (the next one decides again), runs never overlap and a missed tick (the laptop asleep) is not caught up:
max_active_runs=1, catchup off. A failed task emails Omar like the other DAGs (job_radar.alert). Like the others, the tasks run
`python -m job_radar <step>` from the job_radar virtual environment. This file keeps its own few helpers rather than importing
dags/job_radar.py, so the two files never depend on each other.
"""

from __future__ import annotations

import os
import subprocess
from datetime import timedelta
from pathlib import Path

import pendulum
import yaml
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG

ROOT = Path(os.environ.get("JOB_RADAR_ROOT", "/opt/job-radar"))  # where settings.yaml is read from (the tests point it elsewhere)
ON = yaml.safe_load((ROOT / "settings.yaml").read_text(encoding="utf-8"))["outreach"].get("enabled") is True
STOP_FILE = "/opt/job-radar/output/outreach.stop"
START = pendulum.datetime(2026, 10, 1, tz="Africa/Cairo")


def alert(context) -> None:
    """A task that failed emails Omar at once (job_radar/alert.py, in job-radar's own Python): no silent failures."""
    ti = context["task_instance"]
    subprocess.run(["/opt/job-radar-venv/bin/python", "-m", "job_radar.alert", "task", ti.dag_id, ti.run_id, ti.task_id,
                    str(ti.try_number)], cwd="/opt/job-radar", timeout=120, check=False)


DEFAULTS = {"retries": 0, "execution_timeout": timedelta(seconds=240), "on_failure_callback": alert}


def step(name: str, stoppable: bool) -> BashOperator:
    """The step, or, with outreach off in settings.yaml, a skip that costs no Python start (re-read every time Airflow reads this
    file; the step checks its own switch again). A stoppable step also skips while output/outreach.stop exists."""
    if not ON:
        command = "echo 'outreach: off in settings.yaml (outreach.enabled), nothing to do'; exit 99"
    else:
        stop = (f'if [ -e {STOP_FILE} ]; then echo "outreach stopped: output/outreach.stop exists"; exit 99; fi; ' if stoppable else "")
        command = f"{stop}/opt/job-radar-venv/bin/python -m job_radar {name}"
    return BashOperator(task_id=name, cwd="/opt/job-radar", bash_command=command)


with DAG(dag_id="job_radar_outreach", schedule="*/5 * * * *", start_date=START, catchup=False, max_active_runs=1,
         is_paused_upon_creation=False, default_args=DEFAULTS):
    step("outreach_send", stoppable=True)

# at :20, away from the applications DAG's :00 (Airflow runs one task at a time)
with DAG(dag_id="job_radar_outreach_replies", schedule="20 * * * *", start_date=START, catchup=False, max_active_runs=1,
         is_paused_upon_creation=False, default_args={**DEFAULTS, "execution_timeout": timedelta(seconds=300)}):
    step("outreach_replies", stoppable=False)
