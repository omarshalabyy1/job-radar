# job-radar

Finds remote and Egypt jobs, grades them against Omar's CV, tailors CVs and cover letters, and publishes to Neon for Fennec (radar-web).

## Map
- `dags/job_radar.py`: the Airflow DAG (daily 12:00 Cairo). Order: extract → transform → describe → match_skills → eligibility → embed → grade → emails, tailor, publish.
- `job_radar/`: the steps. `sources/` one module per board; `grade.py` scoring; `tailor.py` CV + cover letter; `eligibility.py` Egypt check; `publish.py` / `publish_applications.py` push to Neon; `llm.py` free models first, Claude last.
- `sql/`: local warehouse (raw / core / mart). `settings.yaml`: features and caps. `docs/architecture.md`: the long description (check it against the code).
- `scripts/idempotency_check.py`: proves a rerun changes nothing.

## Done when
`bash check.sh` prints `GATE: PASS` (all tests; about 3 minutes). Paste the last lines in the report.
A change to loading or publishing also passes `python scripts/idempotency_check.py`.

## Rules
- Tailored CVs use only facts from the master CV. Never invent employers, dates, metrics or skills.
- Email goes only to Omar (MAIL_TO). Outreach to founders and recruiters is drafts only.
- Job applications: Omar authorised auto-submit on 2026-11-08, under the guardrails in `docs/superpowers/plans/2026-11-08-auto-apply.md` (score floor, daily cap, allow-list, audit log, kill switch). Never outside those guardrails; never invent an answer (unknown questions go to Omar); Omar passes logins and CAPTCHAs himself, the agent never does.
- Never DROP or TRUNCATE Neon tables from here; radar-web owns the schema.
- Never edit `.env` files or print secret values.
- Stuck, or the same failure twice: stop and write what was tried.

## Skills to load (by task)
- Any new work: `skill-finder`, then `superpowers:brainstorming` and `superpowers:writing-plans`.
- Code: `superpowers:test-driven-development`. Airflow DAG: `authoring-dags`, `testing-dags`, `debugging-dags`.
- SQL / warehouse: `warehouse-and-schema-design`, `data-quality-and-contract-testing`. Neon: `neon-postgres`.
- Scraping a source: `scraping-orchestrator`.
- Claude calls, caching, model ids: `claude-api`.
- Before saying done: `superpowers:verification-before-completion`. Before merge: `/code-review`.

## Agents
- Read `STATE.md` first and update it before you stop. `AGENTS.md` says which files are locked, editable, append-only or Omar's to approve.
