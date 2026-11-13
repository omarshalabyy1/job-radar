# The warehouse's "exit code 2" resets (investigated 2026-10-17)

## What happened

On 2026-10-17 at 09:12:49 UTC, during the 12pm collect, the warehouse (`job-radar-warehouse-1`, Postgres 17.11) logged
`server process (PID 50485) exited with exit code 2`, ended every connection and recovered. It was ready again at
09:14:14. `extract_companies` had an idle connection at that moment; its next query (`load()`, steps.py:90) failed
with `server closed the connection unexpectedly`, so the task and the run turned red. transform, match_skills,
eligibility and publish still ran, on the data that was there.

## What the evidence says

- **Exit code 2 is a SIGQUIT.** A Postgres backend leaves with `_exit(2)` only from its SIGQUIT handler (`quickdie`),
  which Postgres writes so that a SIGQUIT sent to any one backend forces a full reset. A crash or the kernel's OOM
  killer shows as `terminated by signal N` (9 for OOM), and that line does not appear.
- **Not out of memory:** `docker inspect` gives OOMKilled=false, no restart and no memory limit. The Docker VM's kernel
  log (`wsl -d docker-desktop dmesg`) has no OOM, killed-process or hung-task line. Swap use was 59 MB of 4 GB.
- **Not /dev/shm or parallel query:** /dev/shm is 64 MB and 2% used. No `could not resize shared memory segment` error
  appears in any log, and a full shm shows as SIGBUS (signal 7), not exit code 2.
- **Not JIT:** `jit` is already off.
- **Not the warehouse's port:** it is bound to 127.0.0.1 only, and only two failed logins appear: `root` and `postgres`,
  from local docker exec sessions.
- **Not parallel load from Airflow:** `AIRFLOW__CORE__PARALLELISM` is 1, so one task runs at a time.
- **Not job-radar's queries:** the same signature hit other Postgres containers in this VM on other days, with
  different workloads. job-radar-airflow-db (Airflow's own metadata) shows four resets on 2026-10-14 between 23:39 and
  23:58. client-radar-airflow-db shows two on 2026-10-15, at 22:56 and 23:39. Argus's argus-pg shows the same
  signature under long test runs. Each time, a client connection had broken just before (`could not send data to
  client: Broken pipe`).
- **The VM stalled:** reinitialising after the reset took 76 seconds, from 09:12:52 to 09:14:08. Normally it takes
  milliseconds. Free RAM on the laptop was about 0.8 GB then. `extract_companies` had just started its headless
  Chromium browsers. The Docker VM has 4 GB.

- **The same again later that day, with the new connection logging on** (10:39 and 10:52 UTC): each victim PID
  never logged `connection received`. So it was not a client session but a child the postmaster had just forked,
  or another kind of child, such as an autovacuum worker. The 10:52 victim ended `terminated by signal 13: Broken
  pipe` instead of exit code 2. The time from the last log line to the reset varies (0.4 s to 62 s), so it is not
  `authentication_timeout`. After the 10:39 reset, recovery's fsync of the data directory took over 2 minutes
  (`recovery_init_sync_method` is `fsync`). Argus's argus-pg cut its own recovery from 13 minutes by setting it to
  `syncfs`.

## Found later on 2026-10-17: the Docker VM was out of memory

At 11:30 UTC the Docker VM (4 GB) had 66 MB free and 702 MB swapped out. `/proc/pressure/memory` showed "full"
at 25% over 5 minutes: a quarter of the time, every task waited for memory. The load average was 21 to 25 on
6 CPUs. Postgres logged `autovacuum worker took too long to start; canceled`, so a fork took over a minute to
start. Most of the memory, about 3.2 GB, was 52 hung `airflow dags list-runs job_radar -o plain` processes in
job-radar-airflow-1. A `docker exec` poll had started one every 1.5 minutes since 09:56, and none returned. This is
a feedback loop: the starved VM stalls forks, a stalled Postgres child dies and resets the server, and the next
CLI call hangs too.

The poller was the main trigger. It was a run watcher in another Claude session. Its host-side `timeout` killed
only the `docker exec` client, so each hung `airflow dags list-runs` stayed alive in the container. Once the watcher
was stopped and the 52 processes ended, the VM went from 66 MB to 2.3 GB free and memory pressure dropped to almost
zero.

What to do:
- Never poll Airflow's CLI with `docker exec` in a loop. To read a run's state, use `psql` on job-radar-airflow-db
  with a `statement_timeout`; or put the timeout inside the container
  (`docker exec ... timeout 30 airflow ...`), never only on the host.
- To check the VM, run `wsl -d docker-desktop sh -c "free -m; cat /proc/pressure/memory"`. "full" above a few
  percent means the VM is starved.

## Root cause, as far as the logs can show it (before the finding above)

Something sent SIGQUIT to one Postgres backend. It is not job-radar's code or SQL; it happens across every Postgres
container in the Docker Desktop WSL VM, and it lines up with VM stalls under memory pressure. The logs cannot show who
sends the signal: Postgres does not log it, and the WSL VM keeps no process audit. This is an environment problem, not
a job-radar bug.

## What was done

1. **Retries, so one reset does not fail a run:** every free extract task now has `retries=2, retry_delay=2 min`
   (dags/job_radar.py). A retry stores nothing twice (load is keyed on source + link). The paid ones (LinkedIn,
   Apify) are left out: a retry would start a second paid run.
2. **Diagnostics, so the next reset names its victim:** the warehouse now logs every connection and disconnection
   with the client's name (`ALTER SYSTEM`: `log_connections`, `log_disconnections`,
   `log_line_prefix = '%m [%p] %u@%d app=%a '`, applied with `pg_reload_conf()`, no restart). Every job_radar
   connection names its step (`application_name = "job_radar <step>"`, db.py). After the next reset, run
   `docker logs job-radar-warehouse-1 | grep -B5 "exit code 2"`. It shows which step's session the PID served and how
   long it had been open.
3. **No silent failures:** see "Alerts" below.

The same reset can hit Argus's argus-pg. The retry and diagnostics pattern applies there too: retry on
`OperationalError` and log connections with `application_name`.

## Alerts (no silent failures)

- A task that fails for good (its retries spent) emails Omar at once. The DAG's `on_failure_callback` runs
  `python -m job_radar.alert`. Subject `job-radar FAILED: <task>`, with the last 15 lines of the task's log. One
  alert per task a day (output/alerts/).
- The collect's last task, `run_report`, emails one more alert when a step failed:
  `job-radar: published with N steps failing`, or `... the website was not published`.
- Every warning of the day is in the Egypt email under "Problems in this run": a source down, 0 postings where the
  last 7 days had some, a board gone, Ollama down, Apify over its limit. Those warnings alone are reason to send the
  email.
- client-radar does the same: `client-radar FAILED: <task>` alerts, and "Problems since the last email" in its email.

## Again on 2026-11-12 and 2026-11-13: health check timeout raised to 30 s

Four more resets with the same signature: 17:20, 17:21 and 17:57 UTC on 2026-11-12 (the radar nearly idle, only
outreach_send every 5 minutes), and 12:37 UTC on 2026-11-13 (13 minutes after the laptop started, during the collect).
Each victim PID never logged `connection received`, and each reset came within a second of the health check's
`pg_isready` taking over a second to log in and ending in `could not send data to client: Broken pipe`: Docker had
given up on it after its 5 s timeout. Before the 12:37 reset the log was silent for 40 seconds. Three more broken
health checks on 2026-11-12 (18:00, 18:19, 18:32) came without a reset. The Docker VM itself had memory to spare once
it restarted (8 GB, no swap, no pressure); the laptop has 15.8 GB with 8 GB given to WSL, so a stall now points at
Windows running short.

The warehouse's health check timeout is now 30 s (docker-compose.yml), so a stall no longer cuts `pg_isready` off
mid-login. It is not proven to be the trigger; if resets go on, the fix is more free memory on the laptop during the
12pm collect.
