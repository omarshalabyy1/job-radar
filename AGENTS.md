# AGENTS.md: how an agent works in job-radar

Read `CLAUDE.md` (map, rules) and `STATE.md` (where things stand) first. Update `STATE.md` before you stop.

## Surfaces

| Surface | What | Rule |
|---|---|---|
| Locked | `check.sh`, `tests/`, `scripts/idempotency_check.py`, `Rules` in `CLAUDE.md` | Read and propose changes; never weaken a test or the gate to pass it |
| Editable | `job_radar/`, `dags/`, `sql/`, `settings.yaml`, `docs/` | Change with a failing test first |
| Append-only | `STATE.md` "Log", `output/` drafts and reviews | Add lines; never rewrite history |
| Human-controlled | `.env*`, Neon schema, sending any email (only to Omar), applying to a job, merge to `main`, pushing secrets, host settings (Docker/Ollama autostart, Task Scheduler) | Ask first; give the command instead of running it |

## Feedback loop

`bash check.sh` must print `GATE: PASS` (tests plus `scripts/idempotency_check.py`). Paste the last lines in the report. A change to loading or publishing also needs the idempotency step, which the gate runs.

## Run it

Containers and the daily DAG: see the `job-radar` skill. Watch runs through the warehouse (`psql` with a statement timeout), never by looping the `airflow` CLI. `python scripts/heartbeat.py` alerts when no step succeeded in 26 hours.

## Stop rules

Same failure twice, or stuck: stop and write what was tried in the `STATE.md` log. Never print secret values.
