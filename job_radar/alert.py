"""No silent failures (Omar, 2026-10-17): every task of the Airflow DAGs that fails for good (its retries spent) emails him at
once, from the DAG's on_failure_callback, which runs this in job-radar's own Python:

    python -m job_radar.alert task <dag_id> <run_id> <task_id> <try_number>

The email goes where the daily emails go (digest.send: MAIL_TO), subject "job-radar FAILED: <task>", with the last lines of
the task's log (Airflow writes it as JSON lines under /opt/airflow/logs). One alert per task a day: the hourly
applications DAG failing all day sends one, not 24 (output/alerts/<task>-<day> marks it). A run that still published with steps failing is
said by the run_report step (steps.run_report), and the day's warnings are in the Egypt email ("Problems in this run").
An alert that cannot be sent prints why and exits 1, so the callback's own log shows it."""

from __future__ import annotations

import html
import json
import os
import sys
from pathlib import Path

LOGS = Path(os.environ.get("AIRFLOW_LOGS", "/opt/airflow/logs"))
TAIL = 15  # log lines in the email


def log_tail(dag_id: str, run_id: str, task_id: str, try_number: str, logs: Path | None = None) -> list[str]:
    """The last TAIL lines the task printed (its traceback, its WARNINGs), or a line saying the log was not found."""
    path = (logs or LOGS) / f"dag_id={dag_id}" / f"run_id={run_id}" / f"task_id={task_id}" / f"attempt={try_number}.log"
    try:
        events = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                events.append(str(json.loads(line).get("event", "")))
            except ValueError:
                events.append(line)
        return [e for e in events if e.strip()][-TAIL:]
    except OSError as e:
        return [f"(no log at {path}: {e!r})"]


def send(subject: str, lines: list[str]) -> bool:
    """Email the alert (digest.send: MAIL_TO; without Gmail settings it writes it to output/ and returns False)."""
    from .digest import send as send_email
    body = (f'<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px"><b>{html.escape(subject)}</b>'
            f'<pre style="white-space:pre-wrap;font-size:12px">{html.escape(chr(10).join(lines))}</pre></div>')
    return send_email(body, subject, "alert")


def task_failed(dag_id: str, run_id: str, task_id: str, try_number: str, logs: Path | None = None) -> tuple[str, list[str]]:
    subject = f"job-radar FAILED: {task_id}"
    lines = [f"DAG {dag_id}, run {run_id}, try {try_number}", "Last lines of its log:", *log_tail(dag_id, run_id, task_id,
                                                                                                 try_number, logs)]
    return subject, lines


def main(args: list[str]) -> int:
    if len(args) != 5 or args[0] != "task":
        print(__doc__)
        return 2
    from datetime import date
    from .config import OUTPUT
    marker = OUTPUT / "alerts" / f"{args[3]}-{date.today()}"
    if marker.exists():
        print(f"alert: {args[3]} already alerted today ({marker.name})")
        return 0
    try:
        if send(*task_failed(*args[1:])):
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
    except Exception as e:  # the email failed: the phone push says it, and the callback's log
        print(f"alert: not sent: {e!r}"[:300])
        fallback(f"job-radar FAILED: {args[3]}", f"and its alert email failed: {e!r}"[:300])
        return 1
    return 0


def fallback(title: str, message: str) -> None:
    """When an alert email cannot be sent: a phone push (digest.push, ntfy), so a broken alerter is still heard."""
    from .digest import push
    try:
        if not push(title, message):
            print("alert: no phone push either (NTFY_TOPIC is empty)")
    except Exception as e:
        print(f"alert: phone push failed too: {e!r}"[:300])


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    sys.exit(main(sys.argv[1:]))
