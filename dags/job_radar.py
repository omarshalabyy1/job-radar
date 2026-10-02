"""job_radar: every day at 07:00 Cairo time, find the new jobs, rank and score them, email them.

    schema -> extract_boards, extract_portals, extract_email -> transform -> describe -> score -> email

Each task is one step of `python -m job_radar`, run from the job_radar virtual environment. A
source that fails is a warning in its task's log and the task stays green; a failed task (the
warehouse down, the email not sent) is retried once after 10 minutes, and the tasks after it
still run on what is there (all_done). A laptop asleep at 07:00 runs the missed day once when it
wakes (catchup off); every source looks back 72 hours, so nothing is lost.
"""

from __future__ import annotations

from datetime import timedelta

import pendulum
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG


def step(name: str, **kwargs) -> BashOperator:
    return BashOperator(task_id=name, cwd="/opt/job-radar",
                        bash_command=f"/opt/job-radar-venv/bin/python -m job_radar {name}", **kwargs)


with DAG(
    dag_id="job_radar",
    schedule="0 7 * * *",
    start_date=pendulum.datetime(2026, 10, 1, tz="Africa/Cairo"),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=False,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=10), "execution_timeout": timedelta(hours=1)},
):
    extracts = [step("extract_boards"), step("extract_portals"), step("extract_email")]
    step("schema") >> extracts
    extracts >> step("transform", trigger_rule="all_done") >> step("describe") \
        >> step("score", trigger_rule="all_done") >> step("email", trigger_rule="all_done")
