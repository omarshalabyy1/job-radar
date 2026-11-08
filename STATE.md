# STATE.md: where job-radar stands

Read first, update last. One fact per line, dated. Newest first in the Log.

## Now (2026-11-08)

- Stack: warehouse, airflow-db, airflow, tracker all up (compose has `restart: unless-stopped`; it was Docker Desktop that was not running, 2026-11-02 to 11-08).
- Last DAG run: `scheduled__2026-11-08T10:00:00+00:00`, success. Egypt email 37 new jobs, abroad 8.
- Gate: `bash check.sh` prints `GATE: PASS` (tests + idempotency).
- Grading: free models first (Groq, Gemini, OpenRouter), Claude only when none answers (`grade.py`, `llm.py`).
- Embedding: needs Ollama on the laptop at `host.docker.internal:11434` (`nomic-embed-text`). Not started by Windows yet.

## Open

- Branch `radar-fixes-2026-11-08` pushed, no PR, not merged.
- Not scheduled yet (Omar's OS settings): Docker Desktop autostart, Ollama autostart, Task Scheduler running `python scripts/heartbeat.py` daily.
- client-radar containers stopped since mid-October; the heartbeat only covers job-radar.
- Neon (read-only, 2026-11-08): `jobs.job` 2303 rows, 2286 in the last 5 days, against 428 in the warehouse. The gap is rows other publishers added (the publish docs say GitHub runs do this); not verified which publisher.
- Vercel connector needs `/mcp` re-authentication; the Neon connector lists no projects, so Neon was read through the repo's own connection.

## Log

- 2026-11-08: fixed Freehire false alarm (drop_warning needs 10% kept before it warns); removed the publish Neon-count warning; removed Sylndr (Lever 404); `describe` and `tailor` summaries now say why they did less; added `scripts/heartbeat.py`, `AGENTS.md`, `STATE.md`. Searched notes, memory and past chats for "harness": only incidental uses, no X article saved; applied the harness-engineering skill (surfaces, durable state, approval boundaries) in `AGENTS.md`.
