"""python -m job_radar <step> [<step> ...] | all

Steps, in order: schema extract_boards extract_bayt extract_remote extract_startups extract_egypt extract_gulf
extract_workable extract_freehire extract_linkedin extract_companies extract_portals extract_market extract_apify extract_email transform describe
match_skills eligibility embed grade_claude tailor outreach_drafts export_career_ops
email_egypt email_abroad publish run_report
track_applications (its own hourly DAG: your mailbox, read-only)
eligibility, embed, grade_claude, tailor, outreach_drafts and track_applications each have a switch in settings.yaml (features).
Each step is recorded in core.step_run (runlog.py): running when it starts, then ok, warn, failed or off.
Settings come from the environment, or from .env in the repo root (see .env.example).
"""

import os
import signal
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from . import runlog, steps
from .db import connect

STEPS = ["schema", "extract_boards", "extract_bayt", "extract_remote", "extract_startups", "extract_egypt",
         "extract_gulf", "extract_workable", "extract_freehire", "extract_linkedin", "extract_companies", "extract_portals", "extract_market", "extract_apify",
         "extract_email",
         "transform", "describe", "match_skills", "eligibility", "embed", "grade_claude", "tailor", "outreach_drafts", "export_career_ops", "email_egypt",
         "email_abroad", "track_applications", "publish", "publish_applications", "run_report"]

WATCHDOG_SECONDS = 210  # under the 240 s execution_timeout of the extract tasks in dags/job_radar.py


def main() -> None:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    names = STEPS if sys.argv[1:] == ["all"] else sys.argv[1:]
    if not names or any(name not in STEPS for name in names):
        raise SystemExit(__doc__)
    # a lone extract step; not "all" (it runs for minutes), nor linkedin or apify (Apify runs of minutes, under their own timeouts)
    if len(names) == 1 and names[0].startswith("extract_") and names[0] not in ("extract_linkedin", "extract_apify"):
        timer = threading.Timer(WATCHDOG_SECONDS, watchdog, (names[0], datetime.now(timezone.utc)))
        timer.daemon = True
        timer.start()
    if {"eligibility", "grade_claude", "tailor", "outreach_drafts", "track_applications"} & set(names):
        runlog.watch_claude()  # imports the Claude SDK, most of the import time: only the steps that call Claude need it
    try:
        conn = connect()
    except psycopg.OperationalError as e:  # the warehouse container is down: skip, so the run is not a wall of red tasks
        print(f"{' '.join(names)}: the warehouse is unreachable, skipped; the next run catches up ({e!r})"[:300])
        raise SystemExit(99)  # BashOperator's skip code
    with conn:
        for name in names:
            print(f"== {name}")  # outside the capture: the run log keeps what the step itself printed last
            runlog.run(conn, name, getattr(steps, name))


def descendants(pid: int, proc: str = "/proc") -> set[int]:
    """Every child, grandchild ... of `pid`, from one look at `proc`: killing a parent hands its children to PID 1, so
    all must be found before the first kill. Empty where there is no /proc (Windows)."""
    children: dict[int, list[int]] = {}
    for name in filter(str.isdigit, os.listdir(proc) if os.path.isdir(proc) else []):
        try:  # after the last ")" of stat: the process name before it can hold spaces and brackets
            ppid = int(Path(proc, name, "stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):  # it ended while we looked
            continue
        children.setdefault(ppid, []).append(int(name))
    found, todo = set(), [pid]
    while todo:
        for child in children.get(todo.pop(), []):
            found.add(child)
            todo.append(child)
    return found


def leave(code: int) -> None:
    """Flush, kill this process's children, exit at once. Playwright's node driver and Chromium outlive os._exit and keep
    the step's output pipe open, so Airflow's task never saw the end and waited for its timeout (extract_companies,
    2026-10-11)."""
    sys.stdout.flush()
    sys.stderr.flush()
    for child in descendants(os.getpid()):
        try:
            os.kill(child, signal.SIGKILL)
        except OSError:  # already gone
            pass
    os._exit(code)


def watchdog(name: str, since: datetime) -> None:
    """The extract step's Python is still running WATCHDOG_SECONDS after it started: a hang. What its batches already
    committed is kept (extract_companies saves each batch of 50 sites as it ends); the step ends green with a warning,
    its run-log row warn. The row is written in a thread with 10 seconds, so a warehouse that hangs cannot hold the exit."""
    message = f"WARNING {name}: stopped by its {WATCHDOG_SECONDS} s watchdog; work already saved is kept"
    print(message, flush=True)

    def record() -> None:
        try:
            with connect() as log:
                runlog.stop_running(log, name, since, message)
        except Exception as e:  # like runlog.save(): a log that cannot be written is never the reason a step fails
            print(f"WARNING run log: {name} not recorded: {e!r}"[:300], flush=True)

    thread = threading.Thread(target=record, daemon=True)
    thread.start()
    thread.join(10)
    leave(0)


def run() -> None:
    """main(), then leave at once: a worker thread of a site or chunk the time budget gave up on would keep the
    process alive until Airflow killed it (extract_companies, 2026-10-11), and so would a browser it started. A failure
    still prints its traceback and exits 1."""
    code = 0
    try:
        main()
    except SystemExit:
        raise  # the usage text: no step ran
    except BaseException:
        traceback.print_exc()
        code = 1
    leave(code)


if __name__ == "__main__":
    run()
