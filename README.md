# job-radar

Every morning at 07:00 Cairo time, one email with the new data and AI jobs posted in the last
72 hours. It searches Egypt, the UAE, Saudi Arabia, Qatar and remote jobs open to Egypt, for
entry and mid level roles, ranked by fit:

1. AI & Data Engineer
2. Data Engineer
3. AI Engineer (NLP, LLM, agents, RAG)
4. BI Developer (Power BI, Excel)
5. Data Analyst

Senior, lead, manager and architect titles are left out. A job already emailed (same title and
company, on any board) is never emailed twice.

## Boards

| Board | How |
|---|---|
| LinkedIn, Indeed, Bayt | [JobSpy](https://github.com/speedyapply/JobSpy), as a guest |
| Remotive | its public API, one call a day |

**No ban risk to your accounts:** nothing logs in anywhere, so there is no account to ban. The
worst a board can do is rate-limit your internet address for a while (LinkedIn answers HTTP 429),
which costs that board's results for that day only. Searches are small and a few seconds apart.

Not covered: Wuzzuf, which has no public API, and Glassdoor and Google Jobs, which return nothing
for these countries through JobSpy.

## Run it

1. `cp .env.example .env` and fill in a Gmail app password (Google Account > Security >
   2-Step Verification > App passwords). Leave it empty to get `output/digest-<date>.html` instead.
2. `docker compose up -d --build` and open http://127.0.0.1:8081. The `job_radar` DAG is on from
   the start; the first email comes at the next 07:00, or press Trigger to run it now.

A laptop asleep at 07:00 runs the missed day once when it wakes. `dags/job_radar.py` can also be
dropped into any Airflow that has JobSpy installed.

Without Airflow: `python -m venv .venv`, `.venv/Scripts/pip install -r requirements.txt`, then
`.venv/Scripts/python job_radar.py`.

To change the roles, places or ranking, edit `ROLES` and `PLACES` at the top of `job_radar.py`.
