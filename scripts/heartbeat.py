"""Phone alert (ntfy) when job_radar has had no successful step in 26 hours or the warehouse cannot be reached.
Run it from Windows Task Scheduler, outside Docker, so it still speaks when Docker is down.

    python scripts/heartbeat.py        # exit 0 fine, 1 alerted"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_radar import db, digest  # noqa: E402


def stale(last: datetime | None, now: datetime, hours: int = 26) -> bool:
    return last is None or now - last > timedelta(hours=hours)


def last_success() -> datetime | None:
    with db.connect() as conn:
        return conn.execute("SELECT max(started_at) FROM core.step_run WHERE status = 'ok'").fetchone()[0]


def main() -> int:
    try:
        reason = "no successful step in 26 hours" if stale(last_success(), datetime.now(timezone.utc)) else None
    except Exception as e:  # Docker down, wrong password, anything: the radar is not running
        reason = f"warehouse unreachable: {e}"
    if reason is None:
        print("heartbeat: ok")
        return 0
    print(f"heartbeat: {reason}")
    if not digest.push("Job radar is not running", reason):
        print("heartbeat: no phone push either (NTFY_TOPIC is empty)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
