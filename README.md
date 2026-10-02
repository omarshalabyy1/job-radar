# job-radar

Every morning at 07:00 Cairo time: the new data and AI jobs from the job boards, 28,000+ company
career pages and your own inboxes, ranked and scored against your CV, in one email, in a Postgres
warehouse, and in a tracker where you mark what you applied to.

**Roles, best fit first:** 1 AI & Data Engineer · 2 Data Engineer · 3 AI Engineer (NLP, LLM,
agents, RAG) · 4 BI Developer (Power BI, Excel) · 5 Data Analyst.
**Places:** Egypt, UAE, Saudi Arabia, Qatar, and remote jobs open to someone in Egypt.
**Level:** entry and mid; senior, lead, manager and architect titles are left out.
**Target companies** (VOIS, Orange, PwC, Deloitte, DHL, Nestlé, ADIB, EG Bank, e&, Nawy ...) are
starred and listed first, from whichever source finds them.

## Sources

| Source | How |
|---|---|
| LinkedIn, Indeed, Bayt | [JobSpy](https://github.com/speedyapply/JobSpy), as a guest |
| Himalayas, We Work Remotely | public search API, RSS feed |
| 28,000+ company career pages on Greenhouse, Lever, Ashby, Workday, BambooHR, iCIMS, Paylocity | the daily crawl of [job-board-aggregator](https://github.com/Feashliaa/job-board-aggregator) |
| Companies on Workable (Nawy ...) | Workable's public API |
| Your inboxes: job alerts from LinkedIn, Indeed, **Wuzzuf**, Bayt ... and recruiter emails | IMAP, read-only; Claude pulls the jobs out |
| Jooble (local Egyptian and Gulf boards) | official API, with a free key |

**No ban risk to your accounts:** nothing logs in to a job site, so there is no account to ban.
The worst a board can do is rate-limit your internet address for a while (LinkedIn answers HTTP
429), which costs that board's results for that day only.

**Wuzzuf** puts its site behind Cloudflare's bot check, which turns away scripts and headless
browsers alike; getting past it would take bot-detection evasion, which this project does not do.
Turn on Wuzzuf's job alerts for your roles instead: they reach your inbox and the email source
reads them. The same goes for GulfTalent, NaukriGulf, Akhtaboot and Glassdoor.

## How it runs

One Airflow DAG, `job_radar`, one task per step of `python -m job_radar <step>`:

```
schema -> extract_boards, extract_portals, extract_email -> transform -> describe -> score -> email
```

| Layer | Table | What |
|---|---|---|
| raw (bronze) | `raw.job_posting` | every posting each run found, as the source gave it |
| core (silver) | `core.job` | one row per job (same title and company on any board), ranked, placed, scored |
| | `core.application` | your status for a job, set in the tracker |
| | `core.skill` | the skills the demand report counts |
| mart (gold) | `mart.job_status`, `mart.skill_demand`, `mart.jobs_daily` | views for the tracker and Power BI |

- **describe** reads each new job's page for its description (schema.org JobPosting or LinkedIn's
  public page).
- **score**: Claude Haiku 4.5 rates each job 0-100 against your CV, with a one-line reason.
- **email** sends the jobs not emailed yet, by role, target companies first, then by score.
- **Tracker** (http://127.0.0.1:8501): filter the jobs, set saved / applied / interview / offer /
  rejected, and see the skills in demand per role.

## Set up

1. `cp .env.example .env` and fill it in: the warehouse password, a Gmail app password for the
   email, an Anthropic API key, your inboxes (`MAILBOXES`), and optionally a Jooble key.
2. Put your CV in this folder as `cv.pdf`, `cv.md` or `cv.txt` (gitignored).
3. Turn on job alerts for your roles on Wuzzuf, LinkedIn, Indeed and Bayt, sent to one of those inboxes.
4. `docker compose up -d --build`. Airflow is at http://127.0.0.1:8081; the DAG is on from the start,
   so press Trigger to run it now, or wait for 07:00.

A laptop asleep at 07:00 runs the missed day once when it wakes; every source looks back 72 hours.

Claude costs about $0.40-0.70 a day (about $12-20 a month) for 150 jobs and a few job emails; the
first run, with three days of jobs, costs a little more. Without an Anthropic key the pipeline runs
without scores and without the email source.

To change the roles, places, keywords or target companies, edit `job_radar/config.py`.

## Power BI

Connect Power BI Desktop to the warehouse (PostgreSQL, `localhost:5433`, database `jobradar`, user
`jobradar`) and load `mart.job_status`, `mart.skill_demand` and `mart.jobs_daily`.

## Credits

- [JobSpy](https://github.com/speedyapply/JobSpy) (MIT): the LinkedIn, Indeed and Bayt searches.
- [job-board-aggregator](https://github.com/Feashliaa/job-board-aggregator) by Riley Dorrington:
  the company career pages. Its data is [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/),
  so this project stays non-commercial.
- Ideas, rewritten here: scoring each job against the CV from
  [career-ops](https://github.com/career-ops-hq/career-ops) and
  [autopilot-jobhunt](https://github.com/tarunlnmiit/autopilot-jobhunt); the application tracker
  from [jobsync](https://github.com/Gsync/jobsync); Airflow + Postgres + email + Power BI from
  [wuzzuf-etl-pipeline](https://github.com/ahmed-mo505/wuzzuf-etl-pipeline).
