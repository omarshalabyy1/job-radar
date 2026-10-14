"""job_radar: find the new jobs, rank them, have Claude grade them; two email DAGs send them right after.
The collect's time is in settings.yaml (schedule), Cairo time; as shipped:

    job_radar               12pm: schema -> extract_boards, extract_bayt, extract_remote, extract_startups, extract_egypt,
                            extract_gulf, extract_workable, extract_freehire, extract_companies, extract_portals,
                            extract_email (one by one) -> transform -> describe -> match_skills -> eligibility
                            -> embed -> grade_claude -> ranked -> tailor; match_skills -> export_career_ops
    job_radar_email_egypt   after ranked: your home's jobs (Egypt) not emailed yet
    job_radar_email_abroad  after ranked: the jobs everywhere else (remote only) not emailed yet
    job_radar_applications  every hour: track_applications reads your mailbox (read-only) for your applications
    job_radar_backup        3am: pg_dump of the core and raw schemas to output/backups, the last 14 kept

The smart steps have a switch each in settings.yaml (features) and print that they are off when it is false.
embed gives each job a local vector (how close it is to your CV). grade_claude (Claude,
settings.yaml claude) scores each job in ai_places that has a description 0-10 against your CV and learns from
your clicks in the tracker, so a job scored 3 or lower or one you cannot legally take from Egypt
is hidden in the tracker and left out of the emails; tailor drafts a CV and cover letter for the 8+ ones. You
finish and send them in Claude Code with /job-radar (the job-radar skill).

Each task is one step of `python -m job_radar`, run from the job_radar virtual environment.
Airflow runs one task at a time (docker-compose.yml: parallelism), so a collect never swamps a busy
laptop; it takes about 15 minutes, and the emails start once grade_claude has ended, red or green: `ranked`,
a task that always succeeds, carries the Asset (Airflow emits an Asset event only for a task that succeeds). With
features.manual_collect_emails false, only the schedule's runs email (a catch-up after the laptop slept is one):
`ranked` skips itself on a Trigger, whose jobs then go out with the next scheduled collect's emails.
They carry the scores of the jobs graded; when grade_claude fails (it selected jobs and graded none: key,
credit or network) its task is red, the jobs go out ungraded and the next run grades them.

- A laptop asleep or switched off at a run time runs once as soon as it is back (catchup off),
  and Trigger (top right in Airflow) runs it now. Every run, scheduled or not, backfills the
  last 24 hours. Docker Desktop must start when you sign in; the containers start with it.
- No duplicates, ever: runs never overlap (max_active_runs=1), a posting is stored once (raw:
  source + link), a job is one row (core: its normalized title + company, so the same job on
  three boards or reposted is one job) and is emailed once. A re-run, a manual run right after a
  scheduled one, or a retry adds nothing twice.

With the warehouse unreachable every step prints so and exits 99 (BashOperator's skip code): the run's tasks are
skipped, not red, and the next run catches up.

A source that fails is a warning in its task's log and the task stays green; a failed task (the
warehouse down, no network, the email not sent) is not retried (schema is: twice, a minute apart, as the
tracker can hold a lock on a view it drops): the next run catches up, and the tasks after it still run on
what is there (all_done). The last task of the collect, check_extracts, is an empty task behind every extract with
Airflow's default rule (all_success): when schema or any extract did not succeed it is upstream_failed, which turns
the run red, so a run whose extracts never ran is red, not green.

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
from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.sdk import DAG, Asset

SETTINGS = yaml.safe_load(Path("/opt/job-radar/settings.yaml").read_text(encoding="utf-8"))
SCHEDULE = SETTINGS["schedule"]
COLLECTED = Asset("job_radar_collected")  # updated by `ranked`, when a collect has tried to grade its jobs; the emails run on it


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
    step("schema", retries=2, retry_delay=timedelta(seconds=60)) >> extracts
    matched = step("match_skills", trigger_rule="all_done")
    extracts >> step("transform", trigger_rule="all_done") >> step("describe", execution_timeout=timedelta(seconds=150)) >> matched
    embedded = step("embed", trigger_rule="all_done", execution_timeout=timedelta(seconds=240))
    graded = step("grade_claude", trigger_rule="all_done", execution_timeout=timedelta(seconds=600))
    # Airflow emits an asset event only when its task succeeds: on a red grade_claude the emails would never start.
    # So the Asset sits on this task, which ends green whatever grade_claude did. With features.manual_collect_emails
    # false, a run that is not the schedule's (its run_id; run_type renders as "DagRunType.SCHEDULED") skips it (exit 99):
    # no Asset event, no email; its jobs stay not emailed and go out with the next scheduled collect's emails.
    ranked = BashOperator(task_id="ranked", trigger_rule="all_done", outlets=[COLLECTED],
                          bash_command="true" if SETTINGS["features"]["manual_collect_emails"]
                          else '[[ "{{ run_id }}" == scheduled__* ]] || exit 99')
    # all_success, unlike the tasks after the extracts (all_done, they run on what is there): red when schema or an extract is
    extracts >> EmptyOperator(task_id="check_extracts")
    checked = step("eligibility", trigger_rule="all_done")
    matched >> checked >> embedded >> graded >> ranked >> step("tailor", trigger_rule="all_done", execution_timeout=timedelta(seconds=300))
    ranked >> step("outreach_drafts", trigger_rule="all_done", execution_timeout=timedelta(seconds=300))  # cold emails to founders, never sent
    matched >> step("export_career_ops")

# your two emails, right after each collect (a manual one too: they hold only jobs not emailed yet)
for dag_id, name in (("job_radar_email_egypt", "email_egypt"), ("job_radar_email_abroad", "email_abroad")):
    with DAG(dag_id=dag_id, schedule=[COLLECTED], start_date=pendulum.datetime(2026, 10, 1, tz="Africa/Cairo"),
             catchup=False, max_active_runs=1, is_paused_upon_creation=False,
             default_args={"retries": 0, "execution_timeout": timedelta(seconds=240)}):
        step(name)

# your applications as your mailbox tells them, every hour (read-only)
with DAG(dag_id="job_radar_applications", schedule="0 * * * *", start_date=pendulum.datetime(2026, 10, 1, tz="Africa/Cairo"),
         catchup=False, max_active_runs=1, is_paused_upon_creation=False,
         default_args={"retries": 0, "execution_timeout": timedelta(seconds=300)}):
    step("track_applications")

# the warehouse's own data (core and raw; the mart views are made again by schema), every night, in pg_dump's custom
# format (nightly-<day>.dump): restore one with pg_restore. Only the newest 14 nightly dumps are kept; other files stay.
BACKUPS, KEEP_BACKUPS = "/opt/job-radar/output/backups", 14
with DAG(dag_id="job_radar_backup", schedule=cron(["3am"]), start_date=pendulum.datetime(2026, 10, 1, tz="Africa/Cairo"),
         catchup=False, max_active_runs=1, is_paused_upon_creation=False,
         default_args={"retries": 1, "retry_delay": timedelta(minutes=5), "execution_timeout": timedelta(minutes=10)}):
    BashOperator(task_id="pg_dump", bash_command=(
        f"set -euo pipefail; mkdir -p {BACKUPS}; file={BACKUPS}/nightly-$(date +%Y-%m-%d).dump; "
        'PGPASSWORD="$WAREHOUSE_PASSWORD" pg_dump -h "$WAREHOUSE_HOST" -p "$WAREHOUSE_PORT" -U jobradar -d jobradar'
        ' -n core -n raw -Fc -f "$file.part" && mv "$file.part" "$file"; '
        f"ls -1t {BACKUPS}/nightly-*.dump | tail -n +{KEEP_BACKUPS + 1} | xargs -r rm --; "
        f'echo "backup: $file ($(du -h "$file" | cut -f1)), $(ls {BACKUPS}/nightly-*.dump | wc -l) kept"'))
