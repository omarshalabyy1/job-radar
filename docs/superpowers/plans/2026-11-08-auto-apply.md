# Auto-apply agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox syntax. Review this plan with Omar before Task 1.

**Goal:** A 24/7 runner that fills and submits job applications for Omar, using only facts he has approved, inside hard limits, with proof of every submission.

**Architecture:** One warehouse queue (`core.apply_queue`) fed from graded jobs; one runner per board family (ATS adapters, plus LinkedIn and Indeed through sessions Omar logs in himself); Playwright drives the forms; Claude (SDK) only maps form fields to approved answers and never writes new facts. Every attempt writes `core.apply_attempt` with a screenshot and the exact answers sent. A kill switch and caps stop it.

**Tech Stack:** Python 3.10, Playwright (Chromium), Postgres warehouse, Airflow (new DAG `job_radar_apply`, every 15 minutes), Claude via `job_radar/claude.py`, ntfy for alerts.

**Spec:** Omar's requests of 2026-11-08 (auto-submit; LinkedIn and Indeed through sessions he logs in and CAPTCHA-passes himself; comprehensive and intensive).

## Global Constraints

- Facts only from the master CV and `output/applications/application-answers.md`. Never invent employers, dates, metrics, skills, salary, notice period or work authorisation; a question with no approved answer is **parked for Omar**, never guessed.
- Omar passes every login, 2FA and CAPTCHA himself (`scripts/open_blocked.py`); the runner never types a password and never solves a CAPTCHA. A CAPTCHA or login wall mid-run stops that board, sends an ntfy push, and waits.
- Email still goes only to Omar (MAIL_TO). Outreach stays drafts.
- Each board's pace limits and 429/403 holds in `sources/base.py` apply (`output/waits/<host>`); never delete a hold.
- No change touches `.env`, Neon tables are never dropped from here.
- Done = `bash check.sh` prints `GATE: PASS`.

## Review Focus

- A job closed or filled between grading and submit must not be submitted (re-check the page first).
- The same job reached through two boards (LinkedIn and the employer's ATS) must be submitted once.
- A form asking something outside the approved answers must park, not submit with a blank or guessed answer.
- A double-click or a crashed run must not submit twice (idempotent attempt rows; submit marks the row before the click that cannot be undone is confirmed by a receipt check).
- The kill switch and daily cap must hold even when the runner is mid-queue.
- A submit with no confirmation signal must be recorded `unconfirmed`, not `submitted`.

---

## Guardrails (the rules the runner enforces; each has a test)

| Guard | Value | Why |
|---|---|---|
| Score floor | Claude grade >= 8 (`core.claude_grade`), `restriction` empty, years mismatch not named | Only strong fits |
| Freshness | posted < 48 h, page re-fetched open at submit time | He applies early; closed jobs never submitted |
| Eligibility | `features.eligibility` result allows Cairo | Never apply where he cannot work |
| Daily cap | 15 submits a day total, 8 per board family, 1 per company per 14 days | Quality and ban safety |
| Allow-list | adapters listed in `settings.yaml: apply.boards` | New board = new explicit entry |
| Unknown question | park, ntfy, wait | No invented answers |
| Kill switch | `apply.enabled: false` in settings, or file `output/apply.stop`, or `apply.paused` row | Stops within one tick |
| Proof | screenshot + answers JSON + confirmation text per attempt | Audit and dispute |
| Dry run | `apply.mode: dry_run` default: fill to the last step, screenshot, do not click submit | First week runs dry |

## File Structure

- Create `sql/apply.sql` (applied from `schema.sql`): `core.apply_queue(job_id, board, state, reason, queued_at)`, `core.apply_attempt(id, job_id, board, started_at, finished_at, outcome, answers jsonb, screenshot text, confirmation text)`, `core.apply_question(job_id, question, parked_at, answer)`.
- Create `job_radar/apply/queue.py`: choose and lock eligible jobs, enforce guards.
- Create `job_radar/apply/answers.py`: load approved answers; `resolve(question, field_type, options) -> Answer | None`.
- Create `job_radar/apply/runner.py`: one tick: pick, open, fill, submit, record.
- Create `job_radar/apply/adapters/{greenhouse,lever,workable,ashby,smartrecruiters,recruitee,linkedin,indeed}.py`, each `class Adapter` with `can_handle(url)`, `fill(page, job, answers)`, `submit(page) -> Receipt`.
- Create `dags/job_radar_apply.py` (every 15 minutes, `max_active_runs=1`).
- Modify `settings.yaml` (`apply:` block), `docs/architecture.md`, `STATE.md`.
- Tests: `tests/test_apply_queue.py`, `tests/test_apply_answers.py`, `tests/test_apply_runner.py`, `tests/fixtures/apply/<adapter>.html` (saved real forms).

---

### Task 1: Queue and guardrails

**Files:** Create `sql/apply.sql`, `job_radar/apply/queue.py`; Test `tests/test_apply_queue.py`

**Interfaces:**
- Produces: `pick(conn, now: datetime) -> list[Job]` (guards applied, ordered by grade then freshness); `record_attempt(conn, job_id, board, outcome, answers, screenshot, confirmation) -> int`.

- [ ] Failing tests: `test_score_below_8_is_never_picked`, `test_a_restriction_or_years_mismatch_is_never_picked`, `test_daily_cap_total_and_per_board_hold_mid_queue`, `test_one_company_gets_one_submit_per_14_days`, `test_same_job_via_two_boards_is_picked_once` (by `job_key`), `test_kill_switch_file_and_setting_stop_pick`.
- [ ] Run them: FAIL (module missing).
- [ ] Implement `pick` with one SQL query plus the cap counts read from `core.apply_attempt` of today.
- [ ] Run tests: PASS. Commit.

### Task 2: Approved answers and question parking

**Files:** Create `job_radar/apply/answers.py`; Test `tests/test_apply_answers.py`

**Interfaces:**
- Produces: `resolve(question: str, field_type: str, options: list[str] | None) -> str | None`; `park(conn, job_id, question) -> None`.

- [ ] Failing tests: known questions (notice period, salary, work authorisation, years with Python, relocation) resolve from `application-answers.md`; an unknown question returns `None`; a select field returns one of `options` or `None` (never a free guess); `test_no_answer_is_ever_invented` (a fuzzed question set never returns a value not in the answers file or the CV).
- [ ] Implement: exact and normalised match first; Claude is allowed only to *choose among approved answers* (returns an approved key or `None`), never to write text.
- [ ] Tests PASS. Commit.

### Task 3: Runner skeleton and idempotency

**Files:** Create `job_radar/apply/runner.py`; Test `tests/test_apply_runner.py`

**Interfaces:**
- Consumes: `queue.pick`, `answers.resolve`, an `Adapter`.
- Produces: `tick(conn, adapters, now) -> TickResult(submitted, parked, unconfirmed, skipped)`.

- [ ] Failing tests with a fake adapter: `test_closed_page_is_skipped_not_submitted`, `test_submit_marks_the_attempt_before_the_irreversible_click`, `test_a_crash_after_submit_never_submits_twice`, `test_no_confirmation_signal_is_unconfirmed`, `test_dry_run_never_clicks_submit`, `test_unknown_question_parks_and_does_not_submit`.
- [ ] Implement `tick`; `apply.mode` dry_run default.
- [ ] Tests PASS. Commit.

### Task 4: ATS adapters (no login, no CAPTCHA)

**Files:** Create `adapters/greenhouse.py`, `lever.py`, `workable.py`, `ashby.py`, `smartrecruiters.py`, `recruitee.py`; Test per adapter against saved form HTML.

**Interfaces:** `Adapter.can_handle(url: str) -> bool`; `fill(page, job, answers) -> list[Field]`; `submit(page) -> Receipt(confirmed: bool, text: str)`.

- [ ] One adapter per task slice: save a real form as a fixture, write the failing test that fills it and lists the fields it could not answer, implement, pass, commit. Start with Greenhouse and Lever; add the rest one by one.
- [ ] A form with a CAPTCHA widget present: the adapter returns `needs_human` (park the job, ntfy), never submits.
- [ ] CV upload uses the PDF `tailor` wrote for that job; the adapter refuses a job whose draft PDF failed `pdf_problems`.

### Task 5: LinkedIn and Indeed through Omar's own sessions

**Files:** Create `adapters/linkedin.py`, `adapters/indeed.py`; modify `scripts/open_blocked.py` to save a named session per host.

- [ ] Omar logs in and passes any CAPTCHA once in the visible Chromium (`scripts/open_blocked.py linkedin.com indeed.com`); the session is saved under `output/sessions/` (gitignored). The adapters only load that session.
- [ ] Easy Apply / Indeed Apply only; any step that asks a question outside the approved answers parks; any checkpoint, CAPTCHA or "verify it's you" page stops that board for 24 hours and sends ntfy.
- [ ] Hard limits for these two: 5 submits a day each, human-like pacing (30-90 s between actions), no parallel tabs. **Risk, accepted by Omar:** these sites ban automated applying; a flagged account can be restricted. The runner stops on the first warning page.
- [ ] Tests: the session-expired page is detected and stops the board; the daily 5 holds; fixtures of an Easy Apply modal and an Indeed Apply form fill and park correctly.

### Task 6: DAG, settings, kill switch, alerts

**Files:** Create `dags/job_radar_apply.py`; modify `settings.yaml`, `tests/test_dag.py`.

- [ ] `apply:` block in `settings.yaml`: `enabled: false`, `mode: dry_run`, caps, `boards:` allow-list, `score_floor: 8`.
- [ ] DAG every 15 minutes, `max_active_runs=1`, one task `apply_tick`; failure goes to the existing alert path.
- [ ] ntfy on: submit confirmed, job parked with the question, board stopped (CAPTCHA/login/checkpoint), cap reached, kill switch hit.
- [ ] Test: DAG imports, has the schedule, and `enabled: false` makes `apply_tick` a no-op that prints why.

### Task 7: Visibility and rollout

**Files:** Modify the tracker page (Applications), `docs/architecture.md`, `STATE.md`, `heartbeat.py` (alert when the apply DAG should have run and did not).

- [ ] Applications page: queue, parked questions with an answer box (an answer is saved to `core.apply_question` and learned into the answers file only after Omar approves it), today's submits with screenshot links.
- [ ] Rollout, each step needs Omar's explicit go: week 1 dry run on ATS boards only (read every screenshot); week 2 live on Greenhouse and Lever with cap 5; week 3 widen the allow-list; LinkedIn and Indeed last, after he has logged in.
- [ ] `bash check.sh` prints `GATE: PASS`; update `STATE.md` and `docs/architecture.md`.

## Self-review

- Spec coverage: auto-submit (Tasks 3-6), LinkedIn/Indeed via his logins (Task 5), comprehensive/intensive (guardrails table, review focus, rollout), rules updated in `CLAUDE.md`.
- Open decisions for Omar: the caps in the guardrail table, whether the score floor stays 8, and whether LinkedIn/Indeed go live at all after seeing the ban risk.
