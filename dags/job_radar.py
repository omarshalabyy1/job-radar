"""job_radar: find the new jobs and rank them; two email DAGs send them right after. The collect's
time is in settings.yaml (schedule), Cairo time; as shipped:

    job_radar               12pm: schema -> extract_boards, extract_bayt, extract_remote, extract_startups, extract_egypt,
                            extract_gulf, extract_workable, extract_freehire, extract_companies, extract_portals,
                            extract_email (one by one) -> transform -> describe -> match_skills
                            -> export_career_ops
    job_radar_email_egypt   after each collect: your home's jobs (Egypt) not emailed yet
    job_radar_email_abroad  after each collect: the jobs everywhere else (remote only) not emailed yet

No AI runs on this schedule. Claude works only when you call /job-radar in Claude Code: it runs
this DAG, reviews the matches against your CV and tailors applications (the job-radar skill).

Each task is one step of `python -m job_radar`, run from the job_radar virtual environment.
Airflow runs one task at a time (docker-compose.yml: parallelism), so a collect never swamps a busy
laptop; it takes about 15 minutes, and the emails start once match_skills is done (its Asset).

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

Each step stops itself at its time budget (config: 150 s an extract, describe 60 s) and leaves
the rest for the next run. execution_timeout stops a
step that hangs anyway; it allows 90 s more than the budget, because on a busy laptop a task can
need over a minute just to start Python (2026-10-05: tasks killed at 170 s before their work began).

The collects stay inside every source's limits (README: Sources): a site that answers 429 is left
alone for as long as it asks, in every run, and Workable, which allows few searches a day, is
searched in one collect a day. A manual Trigger keeps to the same limits.
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

import pendulum
import yaml
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG, Asset

SCHEDULE = yaml.safe_load(Path("/opt/job-radar/settings.yaml").read_text(encoding="utf-8"))["schedule"]
COLLECTED = Asset("job_radar_collected")  # updated when a collect has ranked its jobs; the emails run on it


def cron(times: list) -> str:
    """["11:30am", "7:30pm"] -> "30 11,19 * * *"; 12am is midnight. One cron line has one minute, so
    the times of a list share theirs."""
    found = [re.fullmatch(r"(\d{1,2})(?::(\d\d))?\s*([ap]m)", str(t).strip().lower()) for t in times]
    if not all(found) or len({m[2] for m in found}) != 1:
        raise ValueError(f"settings.yaml schedule: {times} - write times like 7am or 7:30pm, one minute per line")
    hours = sorted(int(m[1]) % 12 + (12 if m[3] == "pm" else 0) for m in found)
    return f"{int(found[0][2] or 0)} {','.join(map(str, hours))} * * *"


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
    default_args={"retries": 0, "execution_timeout": timedelta(seconds=240)},
):
    extracts = [step(name) for name in ("extract_boards", "extract_bayt", "extract_remote", "extract_startups",
                                        "extract_egypt", "extract_gulf", "extract_workable", "extract_freehire",
                                        "extract_companies", "extract_portals", "extract_email")]
    extracts.append(step("extract_linkedin", execution_timeout=timedelta(seconds=300)))  # one Apify run, up to 280 s
    step("schema") >> extracts
    matched = step("match_skills", trigger_rule="all_done", outlets=[COLLECTED])
    extracts >> step("transform", trigger_rule="all_done") >> step("describe", execution_timeout=timedelta(seconds=150)) >> matched
    matched >> step("export_career_ops")

# your two emails, right after each collect (a manual one too: they hold only jobs not emailed yet)
for dag_id, name in (("job_radar_email_egypt", "email_egypt"), ("job_radar_email_abroad", "email_abroad")):
    with DAG(dag_id=dag_id, schedule=[COLLECTED], start_date=pendulum.datetime(2026, 10, 1, tz="Africa/Cairo"),
             catchup=False, max_active_runs=1, is_paused_upon_creation=False,
             default_args={"retries": 0, "execution_timeout": timedelta(seconds=240)}):
        step(name)
