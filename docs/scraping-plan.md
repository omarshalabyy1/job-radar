# Scraping plan

How job-radar reads each source, and why that way. Measured on 2026-10-05; re-measure when a source
breaks (a refusal, a falling count, empty fields), because these facts belong to the sites and change.

## The same for every source

- **Goal:** the new data and AI jobs in your roles, in your places: Egypt (onsite or hybrid only in
  your Cairo and Giza areas), anywhere else remote only. Most sources are read as an *observation*
  (their newest jobs per search), not a full count of the site; the coverage column says which.
- **Run:** an Airflow task, at 11am and 7pm (Cairo); the extract tasks run side by side, each within a
  150-second budget, and the whole run under 300 seconds. What does not fit waits for the next run.
- **Storage:** every posting as it came (its JSON in `raw.job_posting.payload`), once per source and
  link; then one row per job in `core.job` (the same job on several boards is one job).
- **Checks:** each task logs `<source>: N postings, M new`; dedup by link (raw) and by normalized
  title and company (core); a source that fails is a warning, not a failed run.
- **Limits first, to avoid bans:** one request a second per site, a 429 held for its Retry-After, a
  403 after the real-Chrome retry held 6 hours (`job_radar/sources.py`, `fetch()` and `request()`).
- **Ladder when a site refuses:** plain `requests`, then `curl_cffi` with a real Chrome handshake,
  then a headless browser (Playwright), then your saved session (`scripts/open_blocked.py`). Never a
  CAPTCHA solved by the code, never a password typed by it. LinkedIn is never opened.

## Per source

| Source | Coverage | Pages and fields | URL list | Tool (and the test that picked it) | Speed a run | Risks and fallback |
|---|---|---|---|---|---|---|
| Indeed, Bayt | observation: 30 newest per role and place | JobSpy's columns (title, company, location, job_url, date_posted, description, is_remote) | every role x place (other places remote only) | JobSpy as a guest | up to ~140 s (Bayt cut at the budget) | a 403 or 429 holds the board 6 h |
| Wuzzuf | population: every job in Egypt of the last 24 h | `/api/search/job` ids, then `/api/job` details: title, company, city, postedAt, description | newest first, 50 a page, until a page has nothing from the window | its web app's JSON API, plain requests (search page behind Cloudflare, API not) | a few calls | API change: the keyword search with `post_date` filter also works |
| Tanqeeb (Egypt and 6 Gulf sites) | observation: 5 field pages per site | cards `a.search-job-title-link`, company, place, relative date | `/s/jobs/<field>-jobs?order_by=most_recent` per country site | HTML, plain requests (its keyword search shows a script nothing) | 35 pages, ~22 s, sites side by side | Gulf jobs mostly onsite (dropped by your rule); markup change |
| NaukriGulf | observation: 30 newest per keyword, last 7 days | `spapi/jobapi/search` JSON: Designation, Company.Name, Location, JdURL, LatestPostedDate, jobInfo | 13 keyword searches | hidden API with `curl_cffi` (requests hangs 15 s, curl 200 in 0.6 s); remembered as Chrome-only | 13 calls, ~28 s | no remote filter (title or summary decides); dates run days late |
| GulfTalent | observation: 25 newest per keyword, last 7 days | `api/jobs/search` JSON: title, company, location, is_remote, link, posted_date_ts | 13 keyword searches | hidden API with `curl_cffi` (requests 403, curl 200) | 13 calls, ~19 s | its remote filter returns nothing, so `is_remote` decides |
| Dubizzle Jobs (UAE) | population: every remote job added in the last 7 days | Algolia hits: name, location_list, absolute_url, created_at | one query with the remote filter | the public search index its pages use (the pages sit behind Imperva for requests and curl alike) | 1 call | the public search key can change: then a browser on the live page finds the new one |
| Himalayas | observation: 100 per keyword | search API JSON | per keyword, newest first, until past the window | public API | ~1 s a page | none known |
| We Work Remotely | population of its feed | RSS items: title, region, link, pubDate | one feed | RSS | 1 call | none known |
| Relomote | observation: data and engineering pages open to Egypt | `article` cards, `time[datetime]` | 2 field pages, until past the window | HTML, plain requests | ~3 pages | markup change |
| Remotive | population of its free feed (24 h late) | API JSON: title, company_name, candidate_required_location, url, publication_date | one call | public API; held 6 h after each call (it allows about 4 a day) | 1 call | feed is small (18 jobs) |
| Remote OK, Jobicy, Working Nomads, Arbeitnow | population of each feed, last 7 days (several publish late) | each API's JSON (see `remote_board()` calls) | one call each | public APIs, plain requests | 4 calls | none known |
| DailyRemote | observation: newest in your fields | `article.lst-card`: title link, relative date, place pill | `/remote-jobs?roles=...&sort_by=time&page=N`, until past the window | HTML, plain requests | ~2 pages, ~2 s | company hidden behind its paid plan |
| Remote.co | population of its latest-jobs sitemap (~500), titles like your roles | job page `__NEXT_DATA__`: title, company, postedDate, countries, remoteWorkLevel | sitemap, then only job pages not read before | sitemap + `curl_cffi` (search pages: Akamai challenge for every impersonation) | 1 + a few pages, ~38 s the first run | sitemap or page layout change |
| Workable | observation: each search's jobs of the last day, across every company on Workable | search API JSON | each keyword x place | public API | 234 searches, once a day (it allows few a day) | a 429 holds it for its Retry-After |
| freehire.me | observation: 100 most relevant per keyword, last day | API JSON, only jobs it rates fresh | each keyword x (remote, Egypt) | public API | 26 calls, ~70 s | none known |
| Your companies (204) | population of each company's own board | Workable, Greenhouse, Lever, Ashby APIs; Phenom and SuccessFactors pages; RSS; else the rendered page's links | `settings.yaml` companies, each read once a day | detected per site: an API when the platform has one, else the page; the ladder above when a site refuses (curl_cffi opened 5 of 6 Cloudflare 403s; waiting for quiet pages timed out Bain, Capgemini and IBM, so a page is read 10 s after it loads) | about 45 sites a run within the budget | a site behind a check is marked blocked for your session; McKinsey dropped (only a stealth browser read it: 20 links in 77 s) |
| 28,000-company feed | observation: the aggregator's daily crawl, first seen in the last day | its JSON chunks | the manifest's chunks | one ~75 MB download, only when the feed changed | ~6 s | the feed stops updating |
| Your Gmail alerts | population of your inboxes and spam, last day | links in each email (LinkedIn cards, Wellfound "Learn more" cards ...) | IMAP, read-only | IMAP; every job in a LinkedIn alert kept | ~35 s | an app password revoked |
| Job pages (descriptions) | the new jobs that came without one | schema.org `JobPosting` in each page | up to 300 a run, by site | plain requests, then curl_cffi (never LinkedIn) | 60 s budget | a refused page skips its site this run (no 6 h hold) |

Site-by-site field notes (which rung worked, refusals, timings) live on this laptop in
`~/.scraping-profiles/<site>/notes.md`, outside the repo.
