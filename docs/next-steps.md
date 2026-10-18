# Next steps (2026-10-17)

What is still open after the data-quality fixes of 2026-10-17.

## Skills coverage

On 2026-10-17, 117 of 677 jobs (17%) listed no skills, down from 199 (31%). A job with no description lists none.
What is left:

- **LinkedIn and DailyRemote** are in `NO_FETCH`: their pages are never read. LinkedIn's need a login, and
  DailyRemote hides the text behind its paid plan. Their jobs keep only the skills their titles name.
- **Email alerts (35)**: most are LinkedIn links. The rest are NaukriGulf tracking links stored before
  `publish.py` cleaned links; reading them would mean following a login link.
- **NaukriGulf (21)** stores a ~150-character snippet. `describe` now reads the whole page of a job whose text is
  under 300 characters (`SNIPPET`), but only within the job's first day (`DAYS`). So the jobs stored before
  2026-10-17 keep their snippet until they age out.

## Locations

- Rows stored before 2026-10-17 can still have an empty location, a line of email text, or Capgemini's whole link
  text as their location. New rows do not. A one-time update of `core.job` (as on 2026-10-17 for titles and Arabic
  locations) would fix them. It needs Omar's OK.
- 15 jobs have the place "Unknown location" (email 13, startup_jobs 2): their location names no country that
  `core.geo_pattern` knows.

## Email links

The warehouse keeps email-alert links as they came, tracking parameters included. `publish.py` cleans them before
they reach the website (Omar, 2026-10-17: one place is enough, as the warehouse is private).

## Warehouse resets

See `warehouse-resets.md`. The main trigger was a run watcher that left hung `airflow` CLI processes. Never poll
Airflow's CLI with `docker exec` in a loop.

## GitHub Actions as the collector (2026-10-18)

- `daily-backup.yml` collects at 17 past 3, 11 and 19 UTC and skips a slot when a publish landed on Neon in the last 6
  hours (the `publish` row in `tracker.step_run`). The laptop's Airflow image must be rebuilt before its publishes write
  that row; until then the gate sees only Actions' own.
- `applications.yml` reads Gmail every 15 minutes and publishes the emails and each job's stage (`publish_applications`).
- Both need the repo's Actions secrets (names in the workflow headers). A private repo has 2,000 free Actions minutes a
  month: a 15-minute reader and a 45-minute collect three times a day do not fit; see the decision with Omar.
