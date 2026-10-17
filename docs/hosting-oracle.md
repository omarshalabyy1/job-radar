# Hosting on an Oracle Cloud Always Free VM

Airflow stays; the VM runs the same four containers as your laptop, with
[`docker-compose.oracle.yml`](../docker-compose.oracle.yml) capping their memory (about 4.6 GB in all).
client-radar can share the VM (its own doc, on ports 8082, 8502 and 5434): both stacks need about 8 GB.
Each collect ends with `publish`, which copies the jobs and your grades to Neon, the website's database.

## Before you start

- The Always Free Ampere A1 shape was 4 OCPU / 24 GB; reports say it became 2 OCPU / 12 GB in 2026. Either fits.
  Check Oracle's "Always Free Resources" page and your console.
- "Out of capacity" on A1 is common; try another availability domain, or retry later.
- Oracle reclaims an Always Free instance idle for 7 days (CPU, network and memory all under 20%). The daily
  collect keeps CPU busy for 15 minutes, which may not be enough: watch the first weeks.
- A datacenter IP gets blocked more than your home one. Holds in `output/waits/` handle that; no source is forced.

## Steps (you do these on the VM; nothing here creates an account)

1. Create the VM: Ubuntu 24.04 ARM, 50 GB boot volume. Open no inbound port except SSH (22).
2. Install Docker and Git, then log out and in again:
   ```bash
   sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
   sudo usermod -aG docker $USER
   ```
3. `git clone https://github.com/omarshalabyy1/job-radar && cd job-radar`
4. From the laptop, copy `.env` (it holds your keys, `NEON_DATABASE_URL` and `OWNER_EMAIL` included; never commit it)
   and your saved logins (the VM has no screen for `open_blocked.py`):
   ```bash
   scp .env <vm>:job-radar/.env
   scp -r output/sessions <vm>:job-radar/output/
   ```
   Your `.env` has `JOB_RADAR_OUTPUT=E:/...`? Delete that line on the VM: there the repo's `output/` is used.
5. Start: `docker compose -f docker-compose.yml -f docker-compose.oracle.yml up -d --build`
   (the first build compiles Chromium's dependencies for Arm; allow 10 minutes).
6. Restore your data: copy the newest backup over (`scp output/backups/nightly-<day>.dump <vm>:job-radar/output/backups/`), then
   `docker exec -i job-radar-warehouse-1 pg_restore -U jobradar -d jobradar --clean --if-exists < output/backups/nightly-<day>.dump`
7. Open Airflow and the tracker through a tunnel: `ssh -L 8501:127.0.0.1:8501 -L 8081:127.0.0.1:8081 <vm>`, then
   http://127.0.0.1:8501 and http://127.0.0.1:8081.
8. Trigger `job_radar` once in Airflow and check it like a laptop run; its `publish` task's log ends with
   `publish: N jobs read, ...`.

## Then

- Stop the laptop's stack (`docker compose down` there), so two copies do not email you twice.
- `.github/workflows/daily-backup.yml` checks Neon every day: with nothing from today's collect, its run fails and
  GitHub emails you.
- The 3am backup stays on the VM's disk. Copy it off the box now and then: `scp <vm>:job-radar/output/backups/nightly-*.dump .`
- Update with `git pull` and `docker compose -f docker-compose.yml -f docker-compose.oracle.yml restart airflow tracker`
  (the containers mount the repo).

## Not tested

The override file passes `docker compose config` on the laptop, but nothing has run on an Arm VM yet.
