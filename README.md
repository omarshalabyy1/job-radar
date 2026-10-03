<div align="center">

# job-radar

**Your own job search, on autopilot.** Four times a day it collects the new data and AI jobs from
job boards, company career pages and your own job-alert emails, keeps only the ones that fit you,
and emails them in two clean digests: one for home, one for remote jobs everywhere else.

![Python](https://img.shields.io/badge/Python-3.10-3776AB?logo=python&logoColor=white)
![Airflow](https://img.shields.io/badge/Apache%20Airflow-3-017CEE?logo=apacheairflow&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-17-4169E1?logo=postgresql&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-tracker-FF4B4B?logo=streamlit&logoColor=white)

Runs on your laptop · costs nothing · never logs in anywhere · no duplicates, ever

</div>

---

## What you get

<table>
<tr>
<td width="45%" valign="top"><img src="docs/email.png" alt="The Egypt email on a phone" width="100%"></td>
<td valign="top">

**Two emails, each with only the jobs you have not seen**

| Email | When (Cairo time) | What is in it |
|---|---|---|
| Home (Egypt) | 12pm and 7pm | onsite and hybrid jobs in Cairo or Giza, and Egypt's remote jobs |
| Outside Egypt | 8am and 8pm | remote jobs in the Gulf, Europe, the USA and worldwide |

Inside each email, the further you scroll the more experience a job asks for:
**Entry & junior → Mid level → Senior**, then your roles in your order, then full-time,
part-time, contract and freelance. Jobs at your companies are marked ⭐ and come first.

**Also on your laptop**

- **Tracker** at http://127.0.0.1:8501: filter jobs, mark saved / applied / interview / offer,
  see the skills each role asks for, upload your CV, add companies.
- **Airflow** at http://127.0.0.1:8081: every run, every task's log, and *Trigger* to run now.
- **SQL** at `localhost:5433` (database and user `jobradar`): query the `mart` views.
- **career-ops export** in `output/career-ops/`: the best matches and your CV, ready for
  [career-ops](https://github.com/career-ops-hq/career-ops).

</td>
</tr>
</table>

## Quick start

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/) and a Gmail account.

```bash
git clone https://github.com/omarshalabyy1/job-radar.git && cd job-radar
cp .env.example .env          # then fill it in (below)
docker compose up -d --build  # Airflow, the warehouse and the tracker
```

Fill in `.env` (it never leaves your laptop):

| Setting | What to put |
|---|---|
| `WAREHOUSE_PASSWORD` | any password you choose, letters and digits |
| `GMAIL_USER`, `GMAIL_APP_PASSWORD` | the Gmail that sends the emails, and its [app password](https://myaccount.google.com/apppasswords) (2-Step Verification on) |
| `MAIL_TO` | where the emails go (empty: `GMAIL_USER`) |
| `MAILBOXES` | the inboxes to read job alerts from: `you@gmail.com:apppassword,other@gmail.com:apppassword` |
| `JOOBLE_API_KEY` | optional, a free key from [Jooble](https://jooble.org/api/about) |

Then turn on LinkedIn and Wuzzuf job alerts for your roles, sent to those inboxes, and keep Docker
Desktop running. The first emails arrive at the next email time; *Trigger* in Airflow runs a
collection now.

> After editing `.env`, run `docker compose up -d` so the containers pick it up.

## Make it yours: `settings.yaml`

Everything you would want to change is plain words in [`settings.yaml`](settings.yaml). Edit, save,
and the next run uses it. A schedule change shows in Airflow within a minute.

| Section | What it changes |
|---|---|
| `home` | your home country: onsite and hybrid jobs are kept only here, and it gets its own email |
| `schedule` | when it collects and when each email goes, written `8am`, `12pm`, `7pm` |
| `roles` | the jobs you want, **in your order** (the email follows it): the words a title needs, and what the job boards are searched for |
| `search_words` | the plain search words for Himalayas, Workable and Jooble |
| `too_senior`, `never` | titles to leave out: above your level, or never yours |
| `experience` | the words for entry and senior, and the years that make a job entry (1 or less) or senior (5 or more) |
| `places` | the places, **in email order**, where the boards search, and the words that name each one |
| `onsite_areas`, `other_home_cities` | where at home an onsite job is fine (Cairo, Giza ...), and the cities that are not |
| `remote_words` | how a remote job says so |
| `your_companies` | companies whose jobs get ⭐ and go first |
| `companies` | career pages read every run: a name and a link |

How words match a title, a location or a company:

```yaml
roles:
  - name: Data Engineer
    search: [Data Engineer, ETL Developer, Analytics Engineer]
    title_needs:                      # one word from EACH list
      - [data engineer*, etl, analytics engineer*]

too_senior: [lead, principal, head, director, manager]   # whole words, any case
```

- A word matches as a whole word, in any case: `ai` matches "AI Engineer", not "Airline".
- A trailing `*` matches the start of a word: `engineer*` matches engineer, engineers, engineering.
- English and Arabic both work: `مهندس بيانات*`.

Secrets stay in `.env`. Your skill list (what the tracker counts) is `core.skill` in
[`sql/schema.sql`](sql/schema.sql).

## How it works

```mermaid
flowchart LR
    subgraph S[Sources]
        B[Job boards<br/>Wuzzuf, Indeed, Bayt, Tanqeeb]
        R[Remote boards<br/>Himalayas, We Work Remotely]
        C[Career pages<br/>yours + 28,000 more]
        W[Workable<br/>every company on it]
        G[Your Gmail<br/>alerts, spam included]
    end
    subgraph A[Airflow]
        E[Extract<br/>7 tasks side by side] --> T[Transform<br/>role, place, level, dedupe] --> D[Describe<br/>job pages] --> M[Match skills]
    end
    subgraph P[Postgres]
        RAW[(raw)] --> CORE[(core)] --> MART[(mart)]
    end
    subgraph Y[You]
        EM[Two emails]
        TR[Tracker]
    end
    S --> E --> RAW
    M --> CORE
    MART --> EM & TR
```

Three Airflow DAGs share the work:

| DAG | When | Does |
|---|---|---|
| `job_radar` | 1am, 7am, 11am, 6pm | extract → transform → describe → match_skills → export_career_ops |
| `job_radar_email_egypt` | 12pm, 7pm | emails home's new jobs |
| `job_radar_email_abroad` | 8am, 8pm | emails everywhere else's new jobs |

**The rules that decide what reaches you**

- **Roles:** a title must fit one of your roles; titles above your level or never yours are left out.
- **Places:** onsite or hybrid only at home, in your onsite areas; anywhere else the job must be
  remote. The place comes from the search, else the location, else the title; a job alert whose
  place cannot be told goes under *Unknown location*.
- **No duplicates:** a posting is stored once (source + link); a job is one row (its normalized
  title + company), so the same job on three boards, or reposted, is one job; each job is emailed once.
- **Fast:** a run finishes in under 5 minutes; each step stops at its time budget and leaves the
  rest for the next run. A run missed while the laptop slept runs once it wakes.

**Never done:** logging in to a job site, or getting past a bot check. LinkedIn is only read
through your own alert emails. A careers page that cannot be read shows why in the tracker and is
tried again a day later.

## Sources

| Source | How |
|---|---|
| Wuzzuf | the JSON API its web app calls: every job in Egypt in the window, with its description |
| Indeed, Bayt | [JobSpy](https://github.com/speedyapply/JobSpy) as a guest; remote jobs only outside home |
| Tanqeeb (Bayt, Forasna, NaukriGulf, GulfTalent) | its Egypt site's IT, data, business analyst, Python and internship pages |
| Workable | its public job search, every company on it; remote only outside home |
| Himalayas, We Work Remotely | public search API, RSS feed |
| Your companies | each career page detected once (Workable, Greenhouse, Lever, Ashby, Phenom, SuccessFactors, RSS, or rendered with Playwright), then read every run |
| 28,000+ career pages | the daily crawl of [job-board-aggregator](https://github.com/Feashliaa/job-board-aggregator) |
| Your Gmail | read-only IMAP, inbox and spam: the job links in job-alert emails (any LinkedIn email, Indeed, Wuzzuf, Bayt ...) |

## Troubleshooting

| You see | Do |
|---|---|
| No email arrived | Airflow → the email DAG's last run → its log. "no new jobs, nothing sent" is normal between collections. |
| A `.env` change does nothing | `docker compose up -d` (the containers read `.env` when they are created). |
| A `settings.yaml` change broke the runs | Airflow shows an import error at the top; fix the line it names (YAML: lists in `[...]`, a space after `:`). |
| Docker is slow or stuck | `wsl --shutdown`, then restart Docker Desktop. |

## More

- [Related GitHub projects](docs/related-projects.md): job agents, scrapers and trackers compared.
- Credits: [JobSpy](https://github.com/speedyapply/JobSpy) (MIT) for the Indeed and Bayt searches;
  [job-board-aggregator](https://github.com/Feashliaa/job-board-aggregator) by Riley Dorrington for
  the career pages, whose data is [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/),
  so this project stays non-commercial.
