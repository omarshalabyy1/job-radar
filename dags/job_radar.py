"""job_radar: every 6 hours (01:00, 07:00, 13:00, 19:00 Cairo time), find the new jobs, rank them, email them.

    schema -> extract_boards, extract_remote, extract_egypt, extract_companies, extract_portals,
              extract_email (side by side) -> transform -> describe -> match_skills -> export_career_ops, email

Each task is one step of `python -m job_radar`, run from the job_radar virtual environment. The
extract tasks run in parallel, so a run takes about as long as its slowest source.

- A laptop asleep or switched off at a run time runs once as soon as it is back (catchup off),
  and Trigger (top right in Airflow) runs it now. Every run, scheduled or not, backfills the
  last 24 hours. Docker Desktop must start when you sign in; the containers start with it.
- No duplicates, ever: runs never overlap (max_active_runs=1), a posting is stored once (raw:
  source + link), a job is one row (core: its normalized title + company, so the same job on
  three boards or reposted is one job) and is emailed once. A re-run, a manual run right after a
  scheduled one, or a retry adds nothing twice.

A source that fails is a warning in its task's log and the task stays green; a failed task (the
warehouse down, the email not sent) is retried once after 10 minutes, and the tasks after it
still run on what is there (all_done).
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
    schedule="0 1,7,13,19 * * *",
    start_date=pendulum.datetime(2026, 10, 1, tz="Africa/Cairo"),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=False,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=10), "execution_timeout": timedelta(hours=1)},
):
    extracts = [step(name) for name in ("extract_boards", "extract_remote", "extract_egypt", "extract_companies",
                                        "extract_portals", "extract_email")]
    step("schema") >> extracts
    matched = step("match_skills", trigger_rule="all_done")
    extracts >> step("transform", trigger_rule="all_done") >> step("describe") >> matched
    matched >> [step("export_career_ops"), step("email")]
