"""The run log: python -m job_radar keeps one row per step it runs in core.step_run - when, how long,
ok, warn (it printed a WARNING), failed or off, what the step printed that shows trouble (a WARNING, "skipped",
"0 of ...", "off in settings") and its last printed line, and the tokens and dollars of its Claude calls.
A row is written as 'running' when the step starts and updated when it ends, so a step Airflow killed at its
timeout leaves a 'running' row that never ends (the tracker's Last run tab shows it as killed?); the next start
of the same step marks such a row failed (close_stale).

The Claude steps are not changed for it: watch_claude() wraps the SDK's Messages.create and .parse once,
and every response's usage is added to the step running now.
"""

import re
import sys
import threading
from contextlib import redirect_stdout
from datetime import datetime, timezone
from decimal import Decimal

from .db import connect

# USD per million tokens: input, output, cache write, cache read. A dated model id prices as its prefix.
PRICES = {"claude-sonnet-5-5": ("2", "10", "2.50", "0.20"), "claude-haiku-5-5": ("0.10", "0.50", "0.125", "0.01")}

STALE_SECONDS = 600  # the longest execution_timeout in dags/job_radar.py (grade_claude): a row 'running' longer was killed

_lock = threading.Lock()  # grade_claude calls Claude from 4 threads
_usage: dict[str, list[int]] = {}  # model -> [input, output, cache write, cache read] of the step running now
_watching = False
TROUBLE = re.compile(r"WARNING|skipped|\b0 of\b|off in settings", re.I)  # the lines the summary keeps besides the last
SUMMARY_LINES, LINE_CHARS = 4, 220  # at most 4 lines of trouble and the last line, each cut: under 1,120 characters; 220
# keeps transform's whole "left out" counts line (214 characters with five-digit counts)


def add(model: str, u) -> None:
    with _lock:
        tokens = _usage.setdefault(model, [0, 0, 0, 0])
        for i, n in enumerate((u.input_tokens, u.output_tokens, u.cache_creation_input_tokens or 0,
                               u.cache_read_input_tokens or 0)):
            tokens[i] += n


def cost(usage: dict[str, list[int]]) -> Decimal | None:
    """The exact dollars of these tokens; None when there are none or a model has no price here."""
    if not usage:
        return None
    total = Decimal(0)
    for model, tokens in usage.items():
        price = next((p for name, p in PRICES.items() if model.startswith(name)), None)
        if price is None:
            print(f"WARNING run log: no price for {model}, its cost is left empty")
            return None
        total += sum(n * Decimal(p) for n, p in zip(tokens, price)) / 1_000_000
    return total


def watch_claude() -> None:
    """Count every Messages.create / .parse response. parse posts the request itself, not through create,
    so no call is counted twice. Safe to call again."""
    global _watching
    if _watching:
        return
    from anthropic.resources.messages import Messages
    for name in ("create", "parse"):
        def counted(self, *args, _original=getattr(Messages, name), **kwargs):
            r = _original(self, *args, **kwargs)
            if getattr(r, "usage", None):  # a stream has no usage
                add(r.model, r.usage)
            return r

        setattr(Messages, name, counted)
    _watching = True


class Tee:
    """Writes to the real stdout (the Airflow log) and remembers the lines that show trouble and the last one that
    was not blank."""

    def __init__(self, out):
        self.out, self.last, self.partial, self.trouble = out, None, "", []

    def write(self, s: str) -> int:
        *lines, self.partial = (self.partial + s).split("\n")
        for line in lines:
            if line.strip():
                self.last = line
                if TROUBLE.search(line) and line not in self.trouble:
                    self.trouble.append(line)
        return self.out.write(s)

    def __getattr__(self, name):
        return getattr(self.out, name)

    def lines(self) -> list[str]:
        """Every line that shows trouble, then the last line, as written: the end of a line not yet ended counts."""
        last = self.partial if self.partial.strip() else self.last
        return [*self.trouble, *([last] if last and last not in self.trouble else [])]

    def summary(self) -> str | None:
        """The first SUMMARY_LINES lines of trouble and the last line, each cut to LINE_CHARS, joined by " | "."""
        lines = self.lines()
        if len(lines) > SUMMARY_LINES + 1:
            lines = [*lines[:SUMMARY_LINES], lines[-1]]
        return " | ".join(line.strip()[:LINE_CHARS] for line in lines) or None

    def status(self, name: str) -> str:
        """off: the step said its own switch is off; warn: it printed a WARNING; else ok."""
        lines = self.lines()
        if any(line.startswith(f"{name}: off in settings.yaml") for line in lines):
            return "off"
        return "warn" if any("WARNING" in line for line in lines) else "ok"


def save(row: dict) -> None:
    """On its own connection: a failed step leaves the step's connection aborted, or gone. A log that
    cannot be written is a warning in the Airflow log, never the reason a step fails. The row of a step
    that started is updated when it ends (one row per step and start time)."""
    try:
        with connect() as log:
            log.execute("INSERT INTO core.step_run (step, started_at, finished_at, status, summary, model, input_tokens,"
                        " output_tokens, cache_write_tokens, cache_read_tokens, cost_usd) VALUES (%(step)s, %(started_at)s,"
                        " %(finished_at)s, %(status)s, %(summary)s, %(model)s, %(input)s, %(output)s, %(write)s, %(read)s,"
                        " %(cost)s) ON CONFLICT (step, started_at) DO UPDATE SET finished_at = EXCLUDED.finished_at,"
                        " status = EXCLUDED.status, summary = EXCLUDED.summary, model = EXCLUDED.model,"
                        " input_tokens = EXCLUDED.input_tokens, output_tokens = EXCLUDED.output_tokens,"
                        " cache_write_tokens = EXCLUDED.cache_write_tokens, cache_read_tokens = EXCLUDED.cache_read_tokens,"
                        " cost_usd = EXCLUDED.cost_usd", row)
    except Exception as e:
        print(f"WARNING run log: {row['step']} not recorded: {e!r}"[:300])


def close_stale(log, step: str) -> int:
    """Mark failed each 'running' row of `step` older than STALE_SECONDS: Airflow killed that run (its timeout, or a
    restart) before it could write its end. The row stays, with its start; finished_at stays empty, as it is not known."""
    return log.execute("UPDATE core.step_run SET status = 'failed', summary = 'killed (timeout or Airflow restart)'"
                       " WHERE step = %s AND status = 'running' AND started_at < now() - make_interval(secs => %s)",
                       (step, STALE_SECONDS)).rowcount


def stop_running(log, step: str, since: datetime, summary: str) -> int:
    """The watchdog ended `step`: its 'running' row started at or after `since` (this process) becomes warn."""
    return log.execute("UPDATE core.step_run SET status = 'warn', finished_at = now(), summary = %s"
                       " WHERE step = %s AND status = 'running' AND started_at >= %s", (summary, step, since)).rowcount


def run(conn, name: str, step) -> None:
    """Run the step and record it: a 'running' row first, the real one when it ends; a failure is recorded,
    then raised again."""
    with _lock:
        _usage.clear()
    out, started, error = Tee(sys.stdout), datetime.now(timezone.utc), None
    try:
        with connect() as log:
            close_stale(log, name)
    except Exception as e:  # like save(): a log that cannot be written is never the reason a step fails
        print(f"WARNING run log: stale {name} rows not closed: {e!r}"[:300])
    save({"step": name, "started_at": started, "finished_at": None, "status": "running", "summary": None,
          "model": None, "input": 0, "output": 0, "write": 0, "read": 0, "cost": None})
    try:
        with redirect_stdout(out):
            step(conn)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"[:500]
        raise
    finally:
        with _lock:
            usage = {model: list(tokens) for model, tokens in _usage.items()}
        input_, output, write, read = (sum(column) for column in zip(*usage.values())) if usage else (0, 0, 0, 0)
        save({"step": name, "started_at": started, "finished_at": datetime.now(timezone.utc),
              "status": "failed" if error else out.status(name), "summary": error or out.summary(),
              "model": ", ".join(sorted(usage)) or None, "input": input_, "output": output,
              "write": write, "read": read, "cost": cost(usage)})
