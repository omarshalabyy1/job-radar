"""job_radar: find the new jobs and rank them; two email DAGs send them. The times are in
settings.yaml (schedule), Cairo time; as shipped:

    job_radar               1am, 7am, 11am, 6pm: schema -> extract_boards, extract_remote, extract_egypt,
                            extract_workable, extract_companies, extract_portals, extract_email (side by
                            side) -> transform -> describe -> match_skills -> export_career_ops
    job_radar_email_egypt   12pm and 7pm: your home's jobs (Egypt) not emailed yet
    job_radar_email_abroad  8am and 8pm: the jobs everywhere else (remote only: no hybrid, no onsite) not emailed yet

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
warehouse down, no network, the email not sent) is not retried: the next run catches up, and the
tasks after it still run on what is there (all_done).

A run finishes in under 300 seconds: each step stops itself at its time budget (config: extracts
150 s side by side, describe 60 s) and leaves the rest for the next run; execution_timeout stops
a step that hangs anyway.

Four collects a day stay inside every source's limits (README: Sources): a site that answers 429
is left alone for as long as it asks, in every run, and Workable, which allows few searches a day,
is searched in one of the four. A manual Trigger keeps to the same limits.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pendulum
import yaml
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG

SCHEDULE = yaml.safe_load(Path("/opt/job-radar/settings.yaml").read_text(encoding="utf-8"))["schedule"]


def cron(times: list) -> str:
    """["12pm", "7pm"] -> "0 12,19 * * *": on the hour, 12am is midnight."""
    hours = sorted(int(t[:-2]) % 12 + (12 if t.lower().endswith("pm") else 0) for t in times)
    return f"0 {','.join(map(str, hours))} * * *"


def step(name: str, **kwargs) -> BashOperator:
    return BashOperator(task_id=name, cwd="/opt/job-radar",
                        bash_command=f"/opt/job-radar-venv/bin/python -m job_radar {name}", **kwargs)


with DAG(
    dag_id="job_radar",
    schedule=cron(SCHEDULE["collect"]),
    start_date=pendulum.datetime(2026, 10, 1, tz="Africa/Cairo"),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=False,
    default_args={"retries": 0, "execution_timeout": timedelta(seconds=170)},
):
    extracts = [step(name) for name in ("extract_boards", "extract_remote", "extract_egypt", "extract_workable",
                                        "extract_companies", "extract_portals", "extract_email")]
    step("schema") >> extracts
    matched = step("match_skills", trigger_rule="all_done")
    extracts >> step("transform", trigger_rule="all_done") >> step("describe", execution_timeout=timedelta(seconds=80)) >> matched
    matched >> step("export_career_ops")

# your two emails, each at its own times (a laptop asleep at a time sends once it is back)
for dag_id, name, schedule in (("job_radar_email_egypt", "email_egypt", cron(SCHEDULE["home_email"])),
                               ("job_radar_email_abroad", "email_abroad", cron(SCHEDULE["abroad_email"]))):
    with DAG(dag_id=dag_id, schedule=schedule, start_date=pendulum.datetime(2026, 10, 1, tz="Africa/Cairo"),
             catchup=False, max_active_runs=1, is_paused_upon_creation=False,
             default_args={"retries": 0, "execution_timeout": timedelta(seconds=120)}):
        step(name)
