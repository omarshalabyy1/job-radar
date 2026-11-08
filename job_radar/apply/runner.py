"""One tick of the apply runner (docs/superpowers/plans/2026-11-08-auto-apply.md, Task 3): take the jobs `queue.pick` allows, and for each
one open its page, fill the form from approved answers and the tailored PDFs, then either stop (dry run, parked, skipped) or submit.

What the tick guarantees, each with a test:
- Nothing to pick: nothing done, no browser, no LLM. The runner never calls an LLM at all; answers come from `answers.resolve`.
- A closed page, a missing or faulty draft, or no adapter for the url: the job is `skipped`, its reason stored in `confirmation`.
- A CAPTCHA/login/checkpoint (`needs_human`) or a required field with no approved answer: `parked`, nothing submitted; a CAPTCHA stops
  that board for the rest of the tick (its other jobs are left untouched, so they are picked next tick).
- `apply.mode` is `dry_run` unless it is exactly `live`: fill, screenshot, record `dry_run`, never call `submit`.
- Live: the attempt row is written (and committed) as `unconfirmed` BEFORE `submit` runs, then updated to `submitted` only when the
  receipt confirms. An exception inside `submit` leaves the row `unconfirmed` (the click may have gone out), never `failed`, and a job
  with any row is never picked again, so nothing is submitted twice.
- Any other exception: `failed`, the next job goes on; a job is never retried within a tick.
- Circuit breaker: the first `unconfirmed`, or 3 `parked` in a row (skips, other outcomes in between reset it; skips do not), turns the
  rest of a live tick into a dry run and sets `breaker_tripped`. It is not set in a tick that was dry run already: nothing was flipped.
  The caller (the DAG, Task 6) must turn that into a persistent step back and an ntfy; this module only changes its own tick.
- The kill-switch file is checked before every job, so it holds mid-queue (the caps hold because `pick` counts them for the whole batch).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..config import OUTPUT, SETTINGS
from ..tailor import closed_now, draft_folder, pdf_problems
from . import answers, queue
from .queue import Job, record_attempt

PARKS_TO_TRIP = 3


@dataclass
class TickResult:
    submitted: int = 0
    parked: int = 0
    unconfirmed: int = 0
    skipped: int = 0
    dry_run: int = 0
    failed: int = 0
    breaker_tripped: bool = False


@dataclass(frozen=True)
class Prepared(Job):
    """A picked job plus what an adapter needs from the tailor step: its title and the CV and cover-letter PDFs."""
    title: str = ""
    cv_pdf: Path | None = None
    cover_pdf: Path | None = None


class PlaywrightPages:
    """The default page factory: one headless Chromium for the tick, a fresh context (no shared cookies) per job.
    Starts lazily, so a tick with nothing to do never launches it. `close()` ends it."""

    def __init__(self):
        self._playwright = self._browser = None

    def __call__(self, job):
        if self._browser is None:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch()
        return self._browser.new_context().new_page()

    def close(self):
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()
        self._playwright = self._browser = None


def draft_for(conn, job: Job) -> tuple[Prepared | None, str]:
    """The job with its tailored PDFs (the newest day's draft folder), or (None, why it cannot be used)."""
    row = conn.execute("SELECT title FROM core.job WHERE job_id = %s", (job.job_id,)).fetchone()
    title = row[0] if row else ""
    probe = draft_folder("day", job.company, title, job.job_id)
    folders = sorted((d / probe.name for d in probe.parent.parent.iterdir() if d.is_dir()) if probe.parent.parent.is_dir() else [])
    folders = [f for f in folders if f.is_dir()]
    if not folders:
        return None, "no draft"
    folder = folders[-1]  # day folders sort by date
    pdfs = sorted(folder.glob("*.pdf"))
    cv = next((p for p in pdfs if not p.name.endswith("Cover Letter.pdf")), None)
    cover = next((p for p in pdfs if p.name.endswith("Cover Letter.pdf")), None)
    page_html = folder / "cv.html"
    if cv is None or not page_html.exists():
        return None, f"no draft: no CV pdf or cv.html in {folder.name}"
    try:
        problems = pdf_problems(cv.read_bytes(), page_html.read_text(encoding="utf-8"))
    except Exception as e:  # a corrupt PDF is a faulty draft
        problems = [f"cannot be read ({e!r})"]
    if problems:
        return None, f"draft CV pdf {'; '.join(problems)}"
    return Prepared(job.job_id, job.company, job.board, job.url, job.score, title, cv, cover), ""


def _finish(conn, attempt_id: int, outcome: str, confirmation: str, now: datetime) -> None:
    conn.execute("UPDATE core.apply_attempt SET outcome = %s, confirmation = %s, finished_at = %s WHERE id = %s",
                 (outcome, confirmation, now, attempt_id))
    conn.commit()


def _record(conn, job: Job, outcome: str, now: datetime, **fields) -> int:
    attempt_id = record_attempt(conn, job.job_id, job.board, outcome, when=now, **fields)
    conn.commit()
    return attempt_id


def _screenshot(page, job: Job, now: datetime) -> str:
    path = OUTPUT / "apply" / str(now.date()) / f"{job.job_id}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(path))
    return str(path)


def _apply_to(conn, adapter, job: Prepared, now: datetime, live: bool, page_factory) -> tuple[str, bool]:
    """Run one job's form; returns (outcome recorded, stop this board for the tick)."""
    page, attempt_id, sent = None, None, {}

    def resolve(question: str, field_type: str = "text", options: list[str] | None = None) -> str | None:
        answer = answers.resolve(question, field_type, options)
        if answer is not None:
            sent[question] = answer
        return answer

    try:
        page = page_factory(job)
        page.goto(job.url)
        if adapter.needs_human(page):
            _record(conn, job, "parked", now, confirmation="needs a human: CAPTCHA, login or checkpoint on the page")
            return "parked", True
        missing = adapter.fill(page, job, resolve)
        shot = _screenshot(page, job, now)
        if missing:
            for field in missing:
                answers.park(conn, job.job_id, field.label)
            _record(conn, job, "parked", now, answers=sent, screenshot=shot,
                    confirmation="no approved answer for: " + "; ".join(f.label for f in missing))
            return "parked", False
        if not live:
            _record(conn, job, "dry_run", now, answers=sent, screenshot=shot)
            return "dry_run", False
        attempt_id = _record(conn, job, "unconfirmed", now, answers=sent, screenshot=shot)  # committed before the click
        receipt = adapter.submit(page)
        if receipt.confirmed:
            _finish(conn, attempt_id, "submitted", receipt.text, now)
            return "submitted", False
        _finish(conn, attempt_id, "unconfirmed", receipt.text or "no confirmation on the page", now)
        return "unconfirmed", False
    except Exception as e:
        if attempt_id is not None:  # the click may have gone out: it stays unconfirmed (counted, never retried)
            _finish(conn, attempt_id, "unconfirmed", f"submit raised {e!r}"[:500], now)
            return "unconfirmed", False
        conn.rollback()
        _record(conn, job, "failed", now, answers=sent or None, confirmation=repr(e)[:500])
        return "failed", False
    finally:
        if page is not None:
            try:
                page.close()
            except Exception:
                pass


def tick(conn, adapters, now: datetime, cfg: dict | None = None, page_factory=None) -> TickResult:
    cfg = cfg or SETTINGS["apply"]
    result = TickResult()
    jobs = queue.pick(conn, now, cfg)
    if not jobs:
        return result
    owned = page_factory is None
    page_factory = page_factory or PlaywrightPages()
    live = cfg.get("mode") == "live"  # anything but exactly "live" is a dry run
    was_live, parks_in_a_row, stopped, seen = live, 0, set(), set()
    try:
        for job in jobs:
            if queue.STOP_FILE.exists():  # the kill switch holds mid-queue
                break
            if job.job_id in seen or job.board in stopped:
                continue
            seen.add(job.job_id)
            adapter = next((a for a in adapters if a.can_handle(job.url)), None)
            prepared, why = (None, f"no adapter handles {job.url}") if adapter is None else draft_for(conn, job)
            if prepared is not None and (closed := closed_now(job.url)):
                prepared, why = None, f"closed: {closed}"
            if prepared is None:
                _record(conn, job, "skipped", now, confirmation=why)
                result.skipped += 1
                continue
            outcome, stop_board = _apply_to(conn, adapter, prepared, now, live, page_factory)
            setattr(result, outcome, getattr(result, outcome) + 1)
            if stop_board:
                stopped.add(job.board)
            parks_in_a_row = parks_in_a_row + 1 if outcome == "parked" else 0  # a skip never gets here
            if live and (outcome == "unconfirmed" or parks_in_a_row >= PARKS_TO_TRIP):
                live = False
        result.breaker_tripped = was_live and not live
    finally:
        if owned:
            page_factory.close()
    return result
