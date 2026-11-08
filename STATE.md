# STATE.md: where job-radar stands

Read first, update last. One fact per line, dated. Newest first in the Log.

## Now (2026-11-08)

- Stack: warehouse, airflow-db, airflow, tracker all up (compose has `restart: unless-stopped`; it was Docker Desktop that was not running, 2026-11-02 to 11-08).
- Last DAG run: `scheduled__2026-11-08T10:00:00+00:00`, success. Egypt email 37 new jobs, abroad 8.
- Gate: `bash check.sh` prints `GATE: PASS` (tests + idempotency).
- Grading: free models first (Groq, Gemini, OpenRouter), Claude only when none answers (`grade.py`, `llm.py`).
- Embedding: needs Ollama on the laptop at `host.docker.internal:11434` (`nomic-embed-text`). Not started by Windows yet.

## Open

- Merged to `main` 2026-11-08 (PR #2). fennec PR #2 and client-radar `main` also pushed. fennec CI `check` (db-down smoke tests expect 500, get 404) was already failing before the merge; its `Apply schema to Neon` workflow fails until the repo secret `NEON_OWNER_URL` is added (Omar's to add).
- Not scheduled yet (Omar's OS settings): Docker Desktop autostart, Ollama autostart, Task Scheduler running `python scripts/heartbeat.py` daily.
- client-radar containers stopped since mid-October; the heartbeat only covers job-radar.
- Neon (read-only, 2026-11-08): `jobs.job` 2303 rows, 2286 in the last 5 days, against 428 in the warehouse. The gap is rows other publishers added (the publish docs say GitHub runs do this); not verified which publisher.
- Vercel connector needs `/mcp` re-authentication; the Neon connector lists no projects, so Neon was read through the repo's own connection.

## Log

- 2026-11-08 (CI merged): PR #3 merged to `main` as `31480fc`; the first push-to-main `ci` run passed. Local `bash check.sh` on the PR head printed `GATE: PASS` (433 passed, idempotency `same` on all five tables), run in a sandbox on Python 3.13 and Postgres 16 with Playwright pinned to 1.63.0 there (CI uses 3.10 and Postgres 17). Open: CI reports 434 tests where that local run collects 433, unexplained (no failures or skips in any green run); the `ci/pr-tests` branch is still on GitHub (Omar's to delete).
- 2026-11-08 (CI on pull requests, PR #3, not merged): `.github/workflows/ci.yml` runs `python -m pytest tests -q` on every pull request and push to `main` (Python 3.10, throwaway `postgres:17` on 5433, Playwright Chromium; no secrets). Not run there: `scripts/idempotency_check.py`, so `check.sh` stays the local gate. Run 1 failed (pytest is not in `requirements.txt`: the workflow installs it); run 2 failed on one test: `Saved` in `tests/test_sources.py` called `Path.exists()` on inline HTML, which raises "File name too long" on Linux and returns False on Windows (fixed with Omar's OK, `tests/` is locked). Run 3: 434 passed, none skipped.
- 2026-11-08 (review fixes: heartbeat now watches the daily `transform` step, not the hourly applications steps): fixed Freehire false alarm (drop_warning needs 10% kept before it warns); removed the publish Neon-count warning; removed Sylndr (Lever 404); `describe` and `tailor` summaries now say why they did less; added `scripts/heartbeat.py`, `AGENTS.md`, `STATE.md`. Searched notes, memory and past chats for "harness": only incidental uses, no X article saved; applied the harness-engineering skill (surfaces, durable state, approval boundaries) in `AGENTS.md`.
