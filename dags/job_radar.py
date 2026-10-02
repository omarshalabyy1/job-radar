"""job_radar: every day at 07:00 Cairo time, search the job boards and email the new jobs.

One task running job_radar.py from its own virtual environment. A laptop asleep at 07:00 runs the
missed day once when it wakes (catchup off); the script looks back 72 hours, so nothing is lost.
A board that fails is a warning in the log and the task stays green; only a failed email turns
it red, and it is retried once after 10 minutes.
"""

from __future__ import annotations

from datetime import timedelta

import pendulum
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG

with DAG(
    dag_id="job_radar",
    schedule="0 7 * * *",
    start_date=pendulum.datetime(2026, 10, 1, tz="Africa/Cairo"),
    catchup=False,
    is_paused_upon_creation=False,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=10)},
):
    BashOperator(
        task_id="search_and_email",
        bash_command="/opt/job-radar-venv/bin/python /opt/job-radar/job_radar.py",
        execution_timeout=timedelta(minutes=45),
    )
