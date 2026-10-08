---
name: job-radar
description: Run Omar's job-radar (C:\Users\DELL\GitHub\job-radar) and bring Claude into it - start a collect and check it, then grade the best new matches against his CV, tailor a CV and cover letter for the ones he picks, and help him apply. Use this whenever Omar types /job-radar, or says things like "run the radar", "any new jobs?", "check today's matches", "review my matches", "tailor my CV for this job", "write a cover letter for job 123", "apply to the best ones", "send the job emails now", or asks whether job-radar's last run worked - even if he never names the skill. Airflow's scheduled runs never use Claude; this skill is where Claude works on his job search. Not for the Argus project.
---

# job-radar, with Claude

job-radar finds data and AI jobs for Omar every day: Airflow collects once a day at 12pm (Cairo) and
emails him right after it (about 12:15pm), with no AI involved. This skill is the Claude side: run a collect
on demand, judge the matches against his CV the way a recruiter would, and prepare and send
applications with him.

There are two ways to run it (docs/architecture.md draws them):
1. **Docker + Airflow, on its own:** the 12pm collect and the two emails right after it; he can also press
   Trigger in Airflow any time.
2. **Claude Code + Docker, by hand, when he calls /job-radar:** this skill. It is where his own
   token limit pays for what the schedule never gets: bot checks and logins passed with him and
   persisted for Airflow, the scraping skills on sites that stopped reading, the review and the
   applications.

## The setup, in brief

- Repo: `C:\Users\DELL\GitHub\job-radar` (GitHub omarshalabyy1/job-radar, public). Its README and
  `settings.yaml` are the truth; if this skill disagrees with them, they win - fix this file.
- This skill lives in the repo, at `.claude/skills/job-radar/SKILL.md`; `~/.claude/skills/job-radar`
  is a link to that folder, so there is one copy. Any change to the repo that this file describes
  (containers, DAGs, steps, schedule, sources, tables, rules) updates this file in the same commit,
  and a session that finds them out of step fixes this file first. Public repo: no secrets here.
- Containers: `job-radar-warehouse-1` (Postgres, localhost:5433; database `jobradar`, user
  `jobradar`), `job-radar-airflow-db-1` (Airflow's own records: database and user `airflow`, no
  port outside Docker), `job-radar-airflow-1` (Airflow, http://127.0.0.1:8081, no login; one task
  at a time), `job-radar-tracker-1` (tracker, http://127.0.0.1:8501).
- His rules (settings.yaml and README): onsite or hybrid jobs only in Cairo or Giza, anywhere else
  remote only; above senior is out; every public page is scraped whatever robots.txt or terms say
  (he takes the risk), but LinkedIn is never opened - its jobs come only from his alert emails, and
  every job in a LinkedIn alert is kept; rate limits come first, to avoid bans: `request()` in `job_radar/sources/base.py`
  paces one request a second per site, a 429 holds the site for its Retry-After and a 403 (after
  the real-Chrome retry) for 6 hours, in `output/waits/<host>` - never delete a hold to force a
  source. The code never logs in and never solves a bot check: he does, in
  `scripts/open_blocked.py`, and his session is saved for Airflow.
- Commits only when he asks, as omarshalabyy1 (the repo's own git identity), with no co-author line.

Run SQL with `docker exec job-radar-warehouse-1 psql -U jobradar -d jobradar -At -c "..."`; for
Airflow's tables, `docker exec job-radar-airflow-db-1 psql -U airflow -d airflow -At -c "..."`. In Git Bash, put `MSYS_NO_PATHCONV=1` before a `docker exec`
whose command contains a path like `/opt/...`, and `PYTHONIOENCODING=utf-8` before local Python
that prints job titles (many are Arabic).

## What he can ask

| He types | Do |
|---|---|
| `/job-radar` | steps 1-4: make sure it runs, collect now, review, tailor what he picks |
| `/job-radar review` | steps 1, 3, 4: no collect, review what is there |
| `/job-radar apply <job_id or link>` | steps 4-5 for that one job |
| `/job-radar emails` | send both emails now (see the end) |

## 1. Make sure it runs

`docker ps --filter name=job-radar --format "{{.Names}} {{.Status}}"` should list four containers
Up. If not, `cd C:/Users/DELL/GitHub/job-radar && docker compose up -d`, then wait until Airflow's
scheduler is alive (a heartbeat in the last 30 seconds):

```sql
-- database airflow
SELECT count(*) FROM job WHERE job_type = 'SchedulerJob' AND state = 'running'
  AND latest_heartbeat > now() - interval '30 seconds';
```

Airflow restarts itself when one of its parts stops. If tasks still time out, the Docker VM is
starved - `docker stats --no-stream` shows what eats the CPU (often his other Airflow stack, Argus,
which you never touch): tell him, and let him decide.

Then the sites that need him (the scheduled runs read them with his saved sessions, but a session
expires):

```sql
SELECT company, careers_url, note FROM core.company WHERE platform = 'blocked' ORDER BY company;
```

If any (or he names a site he wants to stay logged in to), start the script in his Terminal panel
with `mcp__terminal__run_in_terminal` - the Bash tool has no stdin, and the script waits for his
Enter after each site:
`cd C:\Users\DELL\GitHub\job-radar; .venv\Scripts\python scripts\open_blocked.py` (or with the
page URLs as arguments). Tell him a Chromium window opens: he gets past the check, accepts the
cookies or logs in himself, then presses Enter in the terminal. Claude never types a password and
never solves a CAPTCHA. The script saves the session to `output/sessions/<host>.json` (gitignored)
and lifts the site's hold; the next run (scheduled or a Trigger) reads the site with it. Read the
terminal (`mcp__terminal__read_terminal`) to confirm "saved ...". Never LinkedIn.

## 1b. When Claude is open: use the scraping skills

This is the time his own token limit pays for, and the scheduled runs never get. For a site the
runs still cannot read (blocked even with his session, a `read failed` note, or a careers page
giving 0 jobs while it lists data jobs), start with the `scraping-orchestrator` skill (also in the
repo's `.claude/skills`): find the site's API or feed, or fix its reader in `job_radar/sources/`,
the module of its group (a company site's new platform goes in `companies.py`: `detect()` and
`READERS`). Add a saved sample and a test in `tests/test_sources.py`, run
`.venv\Scripts\python -m pytest tests`, test it once outside the Airflow container
(`docker compose run --rm --no-deps ...`), and tell him. The fix then runs in Airflow every day.
Every request still goes through `fetch()` in `sources/base.py` (its pace and holds), never past a CAPTCHA,
never LinkedIn. Commit only when he asks.

## 2. Collect now

Trigger once: `MSYS_NO_PATHCONV=1 docker exec job-radar-airflow-1 airflow dags trigger job_radar`
and note the `manual__...` run id. One run at a time: if one is already running, wait for it.

Wait with a background shell loop that polls Airflow's database every 10-15 seconds until the
run's state is `success` or `failed`:

```sql
-- database airflow
SELECT state FROM dag_run WHERE dag_id = 'job_radar' AND run_id = '<run id>';
SELECT task_id, state, round(extract(epoch FROM end_date - start_date))
  FROM task_instance WHERE dag_id = 'job_radar' AND run_id = '<run id>' ORDER BY start_date;
```

Do not poll with the `airflow` command: each call loads all of Airflow, and a loop of them once
starved a run until every task failed. A run takes about 15 minutes: the tasks run one by one,
and both emails follow it on their own (an Airflow Asset), a manual run's too.

Report each task with its result lines, from its log
`/opt/airflow/logs/dag_id=job_radar/run_id=<run id>/task_id=<task>/attempt=1.log`: grep the
`"event":"<source>: ...` lines ("wuzzuf: ...", "freehire: 178 postings, 91 new", "transform: ...")
and any `WARNING`. Normal, not problems: "workable: held for N hours" (one round a day), "remotive:
held" (it allows 4 calls a day), "naukrigulf: 0 postings" or "dubizzle: 0 postings" (Gulf jobs are rarely remote),
"company_portals: the feed is unchanged", "bayt: time budget reached". Several different sites
failing together is the network, not the code; the next run catches up (each looks back a day).

## 3. Review the matches - the part only Claude does

His CV: `SELECT label, text FROM core.cv ORDER BY uploaded_at DESC LIMIT 1;`. If there is none,
ask him to upload it in the tracker (CV tab), or for the file's path - then read it (a PDF with
pypdf) and store it the way the tracker does:
`INSERT INTO core.cv (label, filename, text) VALUES (...) ON CONFLICT (label) DO UPDATE SET
filename = EXCLUDED.filename, text = EXCLUDED.text, uploaded_at = now();`

The best new matches:

```sql
SELECT s.job_id, s.title, s.company, s.place, s.location, s.source, s.job_url, s.role,
       s.target_company, s.skill_matches, s.skills_matched, s.cv_coverage, s.posted_at, s.fresh_level, s.status,
       left(j.description, 3000) AS description
FROM mart.job_status s JOIN core.job j USING (job_id)
WHERE s.first_seen >= current_date - 1 AND s.status NOT IN ('applied', 'rejected', 'ignored')
ORDER BY s.fresh_level, s.target_company DESC, s.skill_matches DESC, s.role_rank
LIMIT 15;
```

He applies within 48 hours of a posting: `fresh_level` 1-3 (within 12, 24, 48 hours of `posted_at`,
the source's own time, else its date, else when the radar first found it) come first; 4 and 5 are over 2
and 3 days; 6 is deleted by tomorrow's transform unless he sets a status. Say how fresh each is.

Grade each against his CV like a recruiter who wants him hired, and is honest:

- **A** apply now: the must-haves are met and the level fits. **B** good, with gaps he can bridge.
  **C** a stretch. **D/F** skip (say why).
- One line of why; the CV strengths it matches; the gaps; red flags - above his level, a location
  that breaks his rules, signs of a ghost or reposted ad, an unknown company.
- A job without a description: read its page (WebFetch) - except LinkedIn, whose pages job-radar
  never fetches; ask him to paste those.

Show a table sorted by grade (#, grade, title @ company, place, why, link), save it as
`output/applications/<YYYY-MM-DD>/review.md`, then ask which ones to tailor (AskUserQuestion with
the A and B jobs, multi-select).

## 4. Tailor what he picks

One folder per job: `output/applications/<YYYY-MM-DD>/<company>-<title>/` (lowercase, dashes),
holding:

- `cv.md` - his CV rewritten for this job: reorder, reword and cut what is true, and use the job's
  own words where his CV backs them. Never invent an employer, title, date, degree, skill or
  number - a CV that claims what he can't show loses the interview. One to two pages.
  Never put a photo on a CV, for any country.
  Layout: an exact copy of his two reference CVs' layout (two PDFs on his Desktop, used with their
  owners' permission; the full spec is the omar-brand skill's `references/cv-look.md`); the content
  is always his own, never theirs. In short: one column, A4, plain
  sans-serif (Calibri-like, about 10 pt), narrow margins, no photo, icons or graphics, so ATS reads
  it. Header centred: his name large and bold in navy, a "Role | Specialty" line under it, then one
  contact line ("Rehab, Cairo, Egypt", phone, email, github.com/omarshalabyy1,
  "Portfolio: omarlabs.dev") separated by " • ". omarlabs.dev always carries the "Portfolio:" label.
  Every link is short and clickable in the PDF (email as mailto, a project repo shown as "GitHub").
  His fixed CV facts (each job's place and dates) are in the same `cv-look.md`; they beat `core.cv`.
  Section headings in bold navy capitals with a full-width navy rule under them, in this order:
  Professional Summary (4-5 lines), Core Skills (groups, each a bold "Label:" then a comma list),
  Professional Experience, Projects, Education, Certifications, Additional (languages). Each role:
  bold title, " — " company | city in grey, dates right-aligned with the length in brackets
  ("Sep 2025 – Present (1 yr)"), then 3-5 bullets that start with a verb and end with a result or
  number. Each project the same, plus an italic "Technologies:" line under its bullets.
- `cover-letter.md` - at most one page: why this company, why him for this role (two or three
  facts from his CV that answer the job's main needs), a short close. Plain, specific English.
  Write it with the copywriting skill so it persuades the reader to call him: open with what he
  can do for them, back each claim with a fact from his CV, end with a clear ask. Short
  paragraphs: two or three sentences each, about 200-250 words in all.
- `notes.md` - the grade, the gaps and how to address them, and three likely interview questions
  with answers drawn from his experience.

Before any of this text goes to him, run the humanizer skill over the cover letter and every
answer to an application form's questions (and the interview answers in `notes.md`), so none of
it reads as AI-written. Humanizer changes the wording only, never the facts.

He wants files to send: offer PDFs (the pdf skill) or Word (the docx skill).

Mark each one saved in the tracker, so it shows there:
`INSERT INTO core.application (job_id, status, note) VALUES (<job_id>, 'saved', 'tailored <date>')
ON CONFLICT (job_id) DO UPDATE SET status = 'saved', note = EXCLUDED.note, updated_at = now();`

## 5. Apply with him - one job at a time, each with his yes

For a form's salary question, run `python scripts\salaries.py <job_id>` on the laptop (or with no
ids for every saved job; `<job_id>=<name>` when the radar has the company in Arabic): it reads the
company's own Glassdoor salaries for that role and country, and the role's market range, with
Playwright in an off-screen window (Glassdoor's bot check stops every headless mode; the company
search is not yet verified past it), into
`output/salaries/<date>.jsonl`. `--all` does every company, country and role in the radar's jobs, by
hand only (never scheduled): it stops after 12 hours and a second run goes on where it stopped.
The figure he asks for follows his rule in
`output/applications/application-answers.md`.

Applying sends his personal data in his name, so it never happens on its own: for one job, show
what will be submitted (which CV, the letter, his answers to the form's questions, each answer
run through the humanizer skill first) and ask. Only
after a clear yes, open the application page in the browser and fill the form from that job's
folder. Logins, passwords and CAPTCHAs are his to do; if a form needs one, hand over. Never apply
through LinkedIn or Indeed - their terms ban automation and accounts get banned: give him the link
and the prepared texts instead. When he confirms it was sent, set the job's status to `applied`
(same SQL, `'applied'`).

When a job says to apply by email, write that email too: to the address in the posting, the job
title in the subject, the tailored CV and cover letter PDFs attached, and a short body (written
with the copywriting skill, three short paragraphs at most, run through humanizer) that always carries his GitHub (github.com/omarshalabyy1) and portfolio
("Portfolio: https://omarlabs.dev") links. Show it with the rest and send it only after his yes, like a form.

## /job-radar emails

`MSYS_NO_PATHCONV=1 docker exec job-radar-airflow-1 airflow dags trigger job_radar_email_egypt`
and the same for `job_radar_email_abroad`; wait on `task_instance` as above, then read each task's
log for "email: sent '...'" or "no new jobs, nothing sent". Each job is emailed once, so an email
sent now leaves less for the next scheduled one.
