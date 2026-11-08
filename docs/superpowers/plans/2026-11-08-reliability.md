# Reliability fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Stop the radar failing silently (a week down unnoticed), drop warnings that are noise, and bring docs and the gate in line with the code.

**Architecture:** One new standalone script reuses `digest.push`; the rest are small edits. No new framework.

**Tech Stack:** Python 3.10, pytest, Postgres (warehouse on localhost:5433), ntfy.sh.

**Spec:** the user's request of 2026-11-08 (fix list in the chat).

## Global Constraints

- Done = `bash check.sh` prints `GATE: PASS`.
- Never print or commit secret values; never edit `.env`; nothing sent to anyone but Omar.
- Compose already has `restart: unless-stopped` on all four services: no change there (the week-long outage was Docker Desktop not starting).
- OS-level settings (Docker/Ollama autostart, Task Scheduler) are Omar's: give the commands, do not run them.

## Review Focus

- Warehouse unreachable (Docker down) must alert, not crash silently.
- No `NTFY_TOPIC`: heartbeat must say so, not pass quietly.
- A run that succeeded 25h59m ago must not alert; 26h01m must.

---

### Task 1: Heartbeat script

**Files:** Create `scripts/heartbeat.py`; Test `tests/test_heartbeat.py`

**Interfaces:**
- Produces: `stale(last_success: datetime | None, now: datetime, hours: int = 26) -> bool`; `main() -> int` (0 fine, 1 alerted).

- [ ] Write failing tests: `test_stale_boundary` (25h59m False, 26h01m True, None True).
- [ ] Run: `.venv/Scripts/python -m pytest tests/test_heartbeat.py -v` -> FAIL (module missing).
- [ ] Implement `stale` and `main`: read last success of `job_radar` from `core.step_run` (latest `started_at` where `status='ok'`); on connection error or `stale`, `digest.push("Job radar", reason)`; print reason; exit 1.
- [ ] Run tests -> PASS.

### Task 2: Remove the misleading publish warning

**Files:** Modify `job_radar/publish.py:301-308` (the `on_neon` count and its WARNING).

- [ ] Confirm no test expects the warning (`grep "jobs read" tests` is empty: done).
- [ ] Delete the `on_neon` query and the warning (Neon also holds GitHub-added jobs, so the ratio means nothing; `if not jobs: raise` stays as the real guard).
- [ ] Run `pytest tests/test_publish.py` -> PASS.

### Task 3: Sylndr

**Files:** Modify `settings.yaml:590`, `docs/companies.md:111`.

- [ ] Remove the Sylndr line from both (its Lever board returns 404 on `api.lever.co` and `jobs.lever.co`).
- [ ] `grep -rn Sylndr settings.yaml docs/companies.md` -> empty.

### Task 4: Docs say who grades

**Files:** Modify `README.md:113`, `docs/architecture.md:93,144`.

- [ ] Replace "Claude scores the closest / 20 closest a day" with: free models grade first (Groq, Gemini, OpenRouter), Claude only when none answers, no daily cap (matches `grade.py` docstring).
- [ ] Re-read each edited line against `grade.py:1-25`.

### Task 5: Gate matches CLAUDE.md

**Files:** Modify `check.sh`.

- [ ] Use `.venv/Scripts/python` when it exists, else `python`.
- [ ] Add the step `python scripts/idempotency_check.py` before `GATE: PASS`.
- [ ] Run `bash check.sh` -> `GATE: PASS`.

### Task 6: Test token placeholder

**Files:** Modify `apify-rotator/test_apify_rotation.py:8`.

- [ ] Set `TOKEN = "apify_test_token"`; run `pytest apify-rotator -q` -> PASS.

### Task 7: Verify and ship

- [ ] Check every task above against its checkbox; run `bash check.sh`.
- [ ] Commit per task on `radar-fixes-2026-11-08`; `git push`.
