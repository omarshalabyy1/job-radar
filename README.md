# job-radar

Every 6 hours (01:00, 07:00, 13:00, 19:00 Cairo time): the new data and AI jobs from the job
boards, Egyptian and Gulf companies' career sites, 28,000+ company career pages and your own
inboxes' job alerts, ranked by how many of **your skills** they ask for, in one email, in a
Postgres warehouse, and in a tracker where you mark what you applied to. Every run backfills the
last 24 hours, never stores or sends a job twice, and costs nothing.

**Roles, best fit first:** 1 AI & Data Engineer · 2 Data Engineer · 3 AI Engineer (NLP, LLM,
agents, RAG) · 4 BI Developer (Power BI, Excel) · 5 Data Analyst. Each role catches its title
variants, in English and Arabic: ETL, SQL and database developers, analytics engineers, data
scientists, ML/MLOps/computer-vision engineers, AI specialists, MIS and data-visualization roles,
product and insights analysts, محلل بيانات, مهندس بيانات ...
**Places:** Egypt, UAE, Saudi Arabia, Qatar, and remote jobs open to someone in Egypt.
**Level:** entry and mid; senior, lead, manager and architect titles are left out. Jobs that state
no years of experience are kept.
**Left out:** data entry, MLOps, DevOps, SRE, QA, technician, teaching, recruiting, accounting.
**Target companies** (VOIS, Orange, PwC, Deloitte, DHL, Nestlé, ADIB, EG Bank, e&, Nawy, Sumerge,
Finaira ...) are starred and listed first, from whichever source finds them.

## Sources

| Source | How |
|---|---|
| Wuzzuf | the JSON API its own web app calls: each query, posted in the last 24 hours, with the full description |
| Indeed, Bayt | [JobSpy](https://github.com/speedyapply/JobSpy), as a guest |
| Bayt, Forasna, NaukriGulf, GulfTalent (and Wuzzuf again) | [Tanqeeb](https://egypt.tanqeeb.com), which gathers them: its job pages for Egypt, UAE, Saudi Arabia and Qatar that robots.txt allows, newest first |
| Every company on Workable: startups and small and mid companies above all | Workable's public job search, each keyword in each place, last day |
| LinkedIn | your LinkedIn job-alert emails only: nothing ever contacts LinkedIn |
| Himalayas, We Work Remotely | public search API, RSS feed |
| **Your companies** (Companies tab of the tracker): Egyptian and Gulf employers and any careers page you add | detected once, then read every run: Workable, Greenhouse, Lever, Ashby, Phenom (Orange, DHL), SuccessFactors (Nestlé, EY), RSS (Deloitte), or any other page rendered with **Playwright** |
| 28,000+ company career pages on Greenhouse, Lever, Ashby, Workday, BambooHR, iCIMS, Paylocity | the daily crawl of [job-board-aggregator](https://github.com/Feashliaa/job-board-aggregator) |
| Your Gmail inboxes: job alerts from LinkedIn, Indeed, **Wuzzuf**, Bayt ... | IMAP, read-only, on your laptop: the job links are read from the alert emails |
| Jooble (local Egyptian and Gulf boards) | official API, with a free key |

**No ban risk to your accounts:** nothing logs in to a job site, and nothing contacts LinkedIn at
all. The worst a board can do is rate-limit your internet address for a while (HTTP 429), which
costs that board's results for that day only.

**What is never done:** logging in, getting past a bot check (a Cloudflare challenge, a CAPTCHA),
or reading a site whose robots.txt forbids it. Wuzzuf's search page sits behind a challenge, so it
is read through its open API instead. A company site that cannot be read shows why in the
Companies tab (forbidden, blocked, or page not found) and is tried again a day later; its jobs
still reach you through the boards and your alert emails, starred.

**Zero cost:** every source is free (guest searches, public APIs and feeds, Gmail), and everything
runs on your laptop with free software: Postgres, Airflow, Streamlit, Docker Desktop (free for
personal use), Power BI Desktop.

## How it runs

One Airflow DAG, `job_radar`, one task per step of `python -m job_radar <step>`:

```
schema -> extract_boards, extract_remote, extract_egypt, extract_companies, extract_portals,
          extract_email (side by side) -> transform -> describe -> match_skills -> email
```

A run takes about as long as its slowest source: the extract tasks run in parallel, Indeed and
Bayt are searched side by side, the company feed downloads 8 chunks at a time, job pages are read
8 sites at a time, and skill matches are stored once per job.

| Layer | Table | What |
|---|---|---|
| raw (bronze) | `raw.job_posting` | each posting once (source + link), as the source first gave it |
| core (silver) | `core.job` | one row per job (same title and company on any board), ranked and placed |
| | `core.application` | your status for a job, set in the tracker |
| | `core.skill` | your skills, from the two courses |
| | `core.job_skill` | which of your skills each job asks for, matched once |
| mart (gold) | `mart.job_status`, `mart.skill_demand`, `mart.jobs_daily` | views for the email, the tracker and Power BI |

- **describe** reads each new job's page for its description (its schema.org JobPosting); never
  a LinkedIn or Wuzzuf page.
- **email** sends the jobs not emailed yet: a header with the day's numbers, one section per role,
  one card per job with ⭐ for your companies, the place, the source, the date and your matched
  skills as chips, best match first; no email when there is nothing new.
- **No duplicates, every run idempotent:** a posting is stored once (source + link), a job is one
  row and is emailed once, and runs never overlap. One job = the same normalized title and company:
  "Data Engineer - Cairo (Hybrid)" at "Acme Ltd." and "Data Engineer" at "ACME" on another board,
  or reposted next week, are one job. A re-run, a manual run right after a scheduled one, or a
  retry adds nothing twice.
- **Tracker** (http://127.0.0.1:8501): filter the jobs, set saved / applied / interview / offer /
  rejected, see which of your skills each role asks for most, and add or remove your companies
  (a name, and its careers page or portal if it has one).

## Set up

1. `cp .env.example .env` and fill it in: the warehouse password, a Gmail app password for the
   email, your inboxes (`MAILBOXES`, one Gmail app password each), and optionally a free Jooble
   key. Gmail app passwords: turn on 2-Step Verification, then
   https://myaccount.google.com/apppasswords. Your normal Gmail password does not work over IMAP.
2. Turn on job alerts for your roles on LinkedIn and Wuzzuf (their jobs come only from these
   emails), and on Indeed and Bayt if you like, sent to those inboxes.
3. `docker compose up -d --build`. Airflow is at http://127.0.0.1:8081; the DAG is on from the start.
   Press Trigger to run it now, or wait for the next 6-hour slot.
4. In Docker Desktop, Settings > General: turn on "Start Docker Desktop when you sign in". The
   containers start with it.

A run missed while the laptop was asleep or off runs once as soon as it is back, and every run,
scheduled, missed or triggered, backfills the last 24 hours.

To add a company, use the tracker's Companies tab. To change the roles, places, keywords or
left-out titles, edit `job_radar/config.py`.

## Power BI

Connect Power BI Desktop to the warehouse (PostgreSQL, `localhost:5433`, database `jobradar`, user
`jobradar`) and load the `mart` views.

## Credits

- [JobSpy](https://github.com/speedyapply/JobSpy) (MIT): the Indeed and Bayt searches.
- [job-board-aggregator](https://github.com/Feashliaa/job-board-aggregator) by Riley Dorrington:
  the company career pages. Its data is [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/),
  so this project stays non-commercial.
- Ideas, rewritten here: ranking jobs by fit from [career-ops](https://github.com/career-ops-hq/career-ops)
  and [autopilot-jobhunt](https://github.com/tarunlnmiit/autopilot-jobhunt); the application tracker
  from [jobsync](https://github.com/Gsync/jobsync); reading Wuzzuf every 6 hours into Postgres
  with Airflow and an email, then Power BI, from
  [wuzzuf-etl-pipeline](https://github.com/ahmed-mo505/wuzzuf-etl-pipeline).
