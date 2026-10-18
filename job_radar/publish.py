"""publish: copy the warehouse's jobs, and Omar's Claude grades of them, to the Neon database behind the website.
Every job but Remotive's, Himalayas' and the site's own email jobs goes, Omar's (in_scope) and website-only alike, with
the wide columns Neon has (radarak-column-spec.md sections 1 and 6) and, for a board job (source '...-board'), only the first 600
characters of its description. Three more published sets, each skipped with one "WARNING publish: skipped: ..." line when Neon lacks the table:
jobs.source_count (one row per source: its jobs on this run, listed or only counted, its credit line), jobs.skill_day (one row per
day per skill, core.skill_day) and jobs.startup (the founder directory, one row per domain, core.startup).

Grain: jobs.job is one row per job, job_id = core.job.job_id (nat_id(job_key) once Neon has the job_key column, see below); jobs.skill is one row per skill, skill = core.skill.skill
(the label, with its track and regular-expression pattern, which app.refresh_scores() matches to a user's skill label);
jobs.job_skill is one row per job per skill the job asks for (job_id, skill), core.job_skill's pairs for the published jobs
and skills; app.job_grade is one row per user per job (user_id, job_id), the user being Omar. Columns follow
radar-web/db/schema.sql (jobs.job, jobs.skill, jobs.job_skill, app.job_grade), in that order.
Idempotent: all seven are upserts on their key (jobs.source_count: source; jobs.skill_day: day, skill; jobs.startup: domain) whose
UPDATE has a WHERE (old columns) IS DISTINCT FROM (new columns) (jobs.job_skill has no other column, so its conflict does
nothing), so a second run changes nothing and writes nothing; the summary line says how many rows each step changed.
counted_at is the start of the day, so a rerun on the same day changes nothing. A track outside Neon's closed list of twelve
(the warehouse's 'Not tech') goes as NULL, a startup team_size under 1 as NULL and a startup location is cut at 200 characters,
the three values Neon's CHECKs would otherwise refuse. The source counts are pruned to this run's sources; skill days and
startups are only added to and updated, never deleted (the spec asks no prune).
One transaction on Neon: the remap, the jobs, the skills, the job skills, the grades, then the prunes, or nothing. When Neon's
jobs.job has the job_key column, a job's id on Neon is nat_id(job_key), the same id whoever writes it, and the upsert carries
job_key. Neon's rows still on a warehouse job_id (job_key null, or set by an earlier publish with the old id) are first
renumbered in place to nat_id(job_key), the key read from core.job or core.job_archive; ON UPDATE CASCADE carries every
user row that points at them, and tracker.application_email.job_id, which has no foreign key, is updated by hand. A row whose
new id or key is already on Neon is left and printed. The next run finds nothing to renumber. On an older Neon without the
column, the jobs go as before with the warehouse's job_id. The jobs prune deletes a jobs.job row that core.job no longer holds, unless it is an email job
(source 'email:...', which the website inserts and the warehouse never holds) or some user has an app.job_status,
app.job_document, app.inbox_job or app.outreach row on it (a job a user saved, wrote a CV or cover letter for, or an alert
or outreach points at is never deleted under them: none of those tables cascades from jobs.job, so a delete would raise
23503 and roll the whole publish back); a table that is not on Neon yet is left out of the check. A deleted job's grade and
job skills go with it (ON DELETE CASCADE). The job skills prune comes
next and deletes the pairs core.job_skill no longer holds; the skills prune deletes a jobs.skill row that core.skill no
longer holds. Last, app.refresh_scores() rebuilds every user's matches.
PUBLISH_PRUNE=0 (set by the GitHub backup runner) skips the prunes so a backup run never deletes rows the laptop's
warehouse owns; the next normal publish prunes.
Empty-source guard: a core.job or core.skill with 0 rows raises before Neon is touched, so a warehouse that is down or
empty can never wipe the site. Neon's url is NEON_DATABASE_URL, never printed or logged; empty, the step raises. It must be the schema owner's: app.*
has FORCE ROW LEVEL SECURITY with an owner_all policy, which is also what lets the prune see every user's job_status.
The grades go to the app.app_user with is_owner = true (Omar's row, created when he first signs in to the website).
OWNER_EMAIL is only a fallback for when no such row exists: it then picks his row by email. Neither found: the grades
are skipped with a line saying so, never a failure.
Every skip or partial publish prints its own line starting "WARNING publish: " (job-radar's run log emails those): a table or
column Neon lacks, no owner, PUBLISH_PRUNE=0, a remap left, and fewer than half of the warehouse-fed jobs.job rows already on Neon read
(a warehouse that came back thin; the 0-row guards still raise)."""

from __future__ import annotations

import hashlib
import os
import re
import time
import urllib.request
from datetime import datetime, timezone
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg

BATCH = 500
JOB = ("job_id", "title", "company", "location", "place", "role", "source", "job_url", "apply_url",
       "company_size", "years_required", "restriction", "posted_at", "first_seen", "description")
# The wide columns, in jobs.job's order (radar-web/db/schema.sql); each is published only when Neon's jobs.job has it.
WIDE = ("work_mode", "countries", "regions", "visa_relocation", "nationals_only", "arabic_required", "salary_min", "salary_max",
        "salary_currency", "salary_period", "salary_min_usd", "salary_max_usd", "salary_min_eur", "salary_max_eur", "salary_plausible",
        "credit", "in_scope", "last_seen", "role_family", "track", "seniority")
TRACKS = ("Data & Analytics", "AI & Machine Learning", "Software Engineering", "Mobile Development", "QA & Testing", "UI/UX Design",
          "DevOps & Cloud", "Security", "ERP & CRM", "Networks & Telecom", "Product & Delivery", "IT & Support")  # jobs.job.track's CHECK
# How the warehouse's value becomes Neon's, where they differ (core.job has no credit column).
WIDE_SQL = {
    "description": "CASE WHEN source LIKE '%%-board' THEN left(description, 600) ELSE description END",
    "countries": "coalesce(countries, '{}')", "regions": "coalesce(regions, '{}')",
    "salary_min_usd": "CASE WHEN salary_plausible THEN salary_min_usd END", "salary_max_usd": "CASE WHEN salary_plausible THEN salary_max_usd END",
    "salary_min_eur": "CASE WHEN salary_plausible THEN salary_min_eur END", "salary_max_eur": "CASE WHEN salary_plausible THEN salary_max_eur END",
    "credit": "CASE source WHEN 'jobicy' THEN 'via Jobicy' WHEN 'arbeitnow' THEN 'via Arbeitnow' WHEN 'jooble' THEN 'via Jooble' END",
    "track": "CASE WHEN track = ANY(%s) THEN track END",
}
KEPT = ("job_status", "job_document", "inbox_job", "outreach")  # app tables that point at jobs.job without a cascade
SOURCE_COUNT = ("source", "jobs", "listed", "credit", "counted_at")
SKILL_DAY = ("day", "skill", "jobs", "total_jobs")
STARTUP = ("domain", "company", "website", "one_liner", "industries", "batch", "location", "region", "team_size", "source", "source_id",
           "source_url", "founder_name", "founder_url", "hiring", "has_remote_roles", "has_internships", "open_source", "visa_friendly",
           "priority", "signal", "board", "careers_url", "first_seen", "updated_at")  # open_roles: core.startup has none; startup_id: Neon's own
STARTUP_SQL = {"team_size": "CASE WHEN team_size > 0 THEN team_size END", "location": "left(location, 200)"}
SKILL = ("skill", "track", "pattern")
JOB_SKILL = ("job_id", "skill")
GRADE = ("user_id", "job_id", "score", "cv", "reason", "restriction", "graded_at")
RETRIES = 2  # a deadlock or serialization failure on Neon (a schema apply running at the same time) is retried this often
PUSH_URL = "https://fennec.omarlabs.dev/api/cron/push"  # radar-web's instant Telegram push, asked for after each publish


def nat_id(key: str) -> int:
    """A job's id on Neon: the first 13 hex digits of md5(key) as a 52-bit number, the same as radar-web's SQL
    ('x' || substr(md5(job_key), 1, 13))::bit(52)::bigint, so one job has one id whoever writes it."""
    return int(hashlib.md5(key.encode()).hexdigest()[:13], 16)


# Query keys that only track the reader of an alert email (LinkedIn's trk/refId/trackingId/midToken, Indeed's tk, utm_*).
TRACKING = re.compile(r"(utm_.*|trk.*|refid|trackingid|midtoken|midsig|eid|lipi|otptoken|alid|tk|tmtk|conmailer|mailerid)", re.I)


def clean_url(url: str) -> str | None:
    """A Gmail alert job's link without Omar's login or tracking tokens (every user can see these jobs on the site):
    a NaukriGulf ccs-tracking link becomes the job page inside its redirect's rUrl, without a query; an Indeed /rc/clk link
    becomes /viewjob?jk=...; any other link loses its TRACKING keys and its fragment. None when the link is an opaque
    click-tracking redirect (links.wellfound.com, ...) that cannot be unwrapped without following it; the caller then keeps the link."""
    parts = urlsplit(url.strip())
    host, query = parts.netloc.lower(), parse_qs(parts.query)
    if host == "ccs-tracking.naukrigulf.com":
        inner = urlsplit(parse_qs(urlsplit(query.get("redirect", [""])[0]).query).get("rUrl", [""])[0])
        ok = inner.netloc.lower().endswith("naukrigulf.com") and len(inner.path) > 1
        return urlunsplit(("https", inner.netloc, inner.path, "", "")) if ok else None
    if host.endswith("indeed.com") and parts.path.startswith("/rc/clk"):
        jk = query.get("jk", [""])[0]
        return f"https://{parts.netloc}/viewjob?jk={jk}" if re.fullmatch(r"[0-9a-f]+", jk) else None
    if host.startswith("links.") or "tracking" in host:
        return None
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING.fullmatch(k)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))


def keys_by_id(conn) -> dict:
    """Every warehouse job_id -> its job_key, from core.job_archive and core.job (core.job wins a key both hold)."""
    by_key = {}
    for table in ("core.job_archive", "core.job"):
        by_key.update((key, job_id) for job_id, key in conn.execute(f"SELECT job_id, job_key FROM {table}"))
    return {job_id: key for key, job_id in by_key.items()}


def remap(cur, keys: dict) -> None:
    """Renumber the Neon jobs still on a warehouse job_id (keys: job_id -> job_key) to nat_id(job_key), setting job_key, and
    the same on tracker.application_email.job_id. Email jobs are never touched. A job whose new id or key is taken by another
    row is printed and left."""
    ids = list(keys)
    given = (ids, [nat_id(keys[i]) for i in ids], [keys[i] for i in ids])
    found = cur.execute(
        "SELECT m.old_id, m.new_id, m.job_key, EXISTS (SELECT 1 FROM jobs.job n WHERE n.job_id <> m.old_id"
        " AND (n.job_id = m.new_id OR n.job_key = m.job_key))"
        " FROM jobs.job j JOIN unnest(%s::bigint[], %s::bigint[], %s::text[]) AS m(old_id, new_id, job_key) ON j.job_id = m.old_id"
        " WHERE m.new_id <> m.old_id AND (j.job_key IS NULL OR j.job_key = m.job_key) AND j.source NOT LIKE 'email:%%'",
        given).fetchall()
    for old, new, _, taken in found:
        if taken:
            print(f"WARNING publish: remap skipped: job {old} would become {new}, already on Neon")
    moves = [row[:3] for row in found if not row[3]]
    if moves:
        old, new, key = zip(*moves)
        cur.execute("UPDATE jobs.job j SET job_id = m.new_id, job_key = m.job_key"
                    " FROM unnest(%s::bigint[], %s::bigint[], %s::text[]) AS m(old_id, new_id, job_key) WHERE j.job_id = m.old_id",
                    (list(old), list(new), list(key)))
        if cur.execute("SELECT to_regclass('tracker.application_email') IS NOT NULL").fetchone()[0]:
            cur.execute("UPDATE tracker.application_email e SET job_id = m.new_id"
                        " FROM unnest(%s::bigint[], %s::bigint[]) AS m(old_id, new_id) WHERE e.job_id = m.old_id",
                        (list(old), list(new)))
    print(f"remapped {len(moves)} jobs to natural-key ids")


def upsert(cur, table: str, columns: tuple, key: tuple, rows: list) -> int:
    """Insert the rows, in batches; a row there already is updated only if a column differs. Returns the rows changed."""
    rest = [c for c in columns if c not in key]
    sql = (f"INSERT INTO {table} AS t ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})"
           f" ON CONFLICT ({', '.join(key)}) DO ")
    if rest:
        sql += (f"UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in rest)}"
                f" WHERE ({', '.join(f't.{c}' for c in rest)}) IS DISTINCT FROM ({', '.join(f'excluded.{c}' for c in rest)})")
    else:  # every column is in the key: nothing to update, so a row there already is left as it is
        sql += "NOTHING"
    changed = 0
    for start in range(0, len(rows), BATCH):
        cur.executemany(sql, rows[start:start + BATCH])
        changed += cur.rowcount  # psycopg 3: the rows affected by all the parameter sets of this executemany
    return changed


def columns(cur, schema: str, table: str) -> set:
    """The column names of a table on Neon (empty when the table is not there)."""
    return {c for (c,) in cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s",
                                      (schema, table))}


def publish(conn) -> None:
    """The publish below, retried when Neon rolls its transaction back for a deadlock (40P01) or a serialization failure
    (40001), as when radar-db's schema apply locks jobs.job mid-run. Nothing was committed, so a retry starts clean."""
    for attempt in range(RETRIES + 1):
        try:
            publish_once(conn)
        except (psycopg.errors.DeadlockDetected, psycopg.errors.SerializationFailure) as e:
            if attempt == RETRIES:
                raise
            print(f"WARNING publish: Neon rolled back ({type(e).__name__}); retry {attempt + 1} of {RETRIES} in {10 * (attempt + 1)} s")
            time.sleep(10 * (attempt + 1))
        else:
            push()
            return


def push() -> None:
    """Asks the website to send linked users their new matches on Telegram now (radar-web GET /api/cron/push, CRON_SECRET as the
    bearer token; safe to call any number of times). The data is already on Neon, so a failure is a WARNING line, never a failed step."""
    secret = os.environ.get("CRON_SECRET", "").strip()
    if not secret:
        print("WARNING publish: no CRON_SECRET in .env; the Telegram push was not asked for")
        return
    request = urllib.request.Request(PUSH_URL, headers={"Authorization": f"Bearer {secret}"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            print(f"push: {response.read(300).decode('utf-8', 'replace')}")
    except OSError as e:  # URLError, HTTPError and timeouts; the message never holds the token
        print(f"WARNING publish: the Telegram push failed: {e}")


def publish_once(conn) -> None:
    started = datetime.now(timezone.utc)
    url = os.environ.get("NEON_DATABASE_URL", "").strip()
    if not url:  # a publish that cannot run fails the step (and the DAG's failure email), it is never a quiet skip
        raise RuntimeError("publish: no NEON_DATABASE_URL in .env; nothing was published")
    prune = os.environ.get("PUBLISH_PRUNE", "1").strip() != "0"
    # Remotive's and Himalayas' terms allow counting their jobs, not republishing them. Every other job goes, Omar's (in_scope)
    # and website-only alike, with a short description excerpt for a board job (radarak-column-spec.md section 6).
    # All the wide columns are read; those Neon's jobs.job lacks are dropped below, once Neon is open.
    wide = JOB + WIDE
    jobs = conn.execute(f"SELECT {', '.join(WIDE_SQL.get(c, c) + ' AS ' + c for c in wide)}, job_key FROM core.job"
                        " WHERE source NOT IN ('remotive', 'himalayas')"
                        " AND source NOT LIKE 'email:%%'"  # 'email:...' is the site's private email-job namespace
                        " ORDER BY job_id", (list(TRACKS),)).fetchall()
    if not jobs:
        raise RuntimeError("publish: core.job has 0 rows; Neon is left untouched")
    # A Gmail alert job (source 'email', owner-only on Neon) goes with clean links (clean_url); a link that cannot be
    # unwrapped goes as it is, so the job stays (Omar's choice: keep these jobs, never prune them for a link).
    src, job_url, apply_url = (wide.index(c) for c in ("source", "job_url", "apply_url"))
    for i, row in enumerate(jobs):
        if row[src] == "email":
            row = list(row)
            row[job_url] = clean_url(row[job_url]) or row[job_url]
            row[apply_url] = row[apply_url] and (clean_url(row[apply_url]) or row[apply_url])
            jobs[i] = tuple(row)
    ids = {row[0] for row in jobs}
    skills = conn.execute(f"SELECT {', '.join(SKILL)} FROM core.skill ORDER BY skill").fetchall()
    if not skills:
        raise RuntimeError("publish: core.skill has 0 rows; Neon is left untouched")
    job_skills = conn.execute("SELECT job_id, skill FROM core.job_skill WHERE job_id = ANY(%s)"
                              " AND skill IN (SELECT skill FROM core.skill) ORDER BY job_id, skill", (list(ids),)).fetchall()
    if not job_skills:  # else the job skills prune below would wipe jobs.job_skill on Neon
        raise RuntimeError("publish: core.job_skill has 0 rows for the published jobs; Neon is left untouched")
    grades = conn.execute("SELECT job_id, score, cv, reason, restriction, graded_at FROM core.claude_grade").fetchall()
    # One row per source: its jobs on this run, listed unless Remotive or Himalayas, its credit line; counted_at is the day (idempotent within it).
    counts = conn.execute(f"SELECT source, count(*)::integer, source NOT IN ('remotive', 'himalayas'), {WIDE_SQL['credit']},"
                          " date_trunc('day', now()) FROM core.job WHERE source NOT LIKE 'email:%%' GROUP BY source ORDER BY source").fetchall()
    days = conn.execute("SELECT day, skill, jobs, total_jobs FROM core.skill_day WHERE skill IN (SELECT skill FROM core.skill)"
                        " ORDER BY day, skill").fetchall()
    startups = conn.execute(f"SELECT {', '.join(STARTUP_SQL.get(c, c) + ' AS ' + c for c in STARTUP)} FROM core.startup ORDER BY domain").fetchall()
    keys = keys_by_id(conn)  # read before Neon is touched, for the remap
    try:
        neon = psycopg.connect(url, connect_timeout=30)  # Neon wakes slowly
    except psycopg.Error:
        raise RuntimeError("publish: cannot connect to Neon; check NEON_DATABASE_URL") from None  # the error can quote the url
    with neon, neon.cursor() as cur:  # one transaction: committed when the block ends, rolled back when it raises
        have = columns(cur, "jobs", "job")  # the columns Neon's jobs.job has; the rest are left out of the upsert
        pick = [i for i, c in enumerate(wide) if c in have or c in JOB]
        cols = tuple(wide[i] for i in pick)
        jobs = [tuple(row[i] for i in pick) + (row[-1],) for row in jobs]  # job_key stays last
        if absent := [c for c in WIDE if c not in have]:
            print(f"WARNING publish: skipped: jobs.job columns not on Neon yet: {', '.join(absent)}")
        # Counted before the upsert; the site's own email jobs are not the warehouse's, so they are left out.
        on_neon = cur.execute("SELECT count(*) FROM jobs.job WHERE source NOT LIKE 'email:%%'").fetchone()[0]
        if len(jobs) * 2 < on_neon:
            print(f"WARNING publish: only {len(jobs)} jobs read, Neon has {on_neon}")
        # Remove this check once radar-db's jobs.job.job_key is applied everywhere.
        if "job_key" in have:
            remap(cur, keys)
            new = {row[0]: nat_id(row[-1]) for row in jobs}  # warehouse job_id -> the id on Neon
            ids = set(new.values())
            job_skills = [(new[j], s) for j, s in job_skills]
            grades = [(new[g[0]], *g[1:]) for g in grades if g[0] in new]
            changed = upsert(cur, "jobs.job", cols + ("job_key",), ("job_id",), [(new[row[0]], *row[1:]) for row in jobs])
        else:
            grades = [g for g in grades if g[0] in ids]
            changed = upsert(cur, "jobs.job", cols, ("job_id",), [row[:-1] for row in jobs])
        skilled = upsert(cur, "jobs.skill", SKILL, ("skill",), skills)
        # Remove this check once radar-db's schema (jobs.job_skill) is applied everywhere.
        has_job_skill = cur.execute("SELECT to_regclass('jobs.job_skill') IS NOT NULL").fetchone()[0]
        if has_job_skill:
            job_skilled = upsert(cur, "jobs.job_skill", JOB_SKILL, JOB_SKILL, job_skills)
        else:
            job_skilled = 0
            print("WARNING publish: job skills skipped: jobs.job_skill not on Neon yet")
        # The three sets below are each skipped, with one line, when Neon does not have the table yet.
        counted = dayed = startuped = 0
        has_counts, has_days, startup_cols = (columns(cur, "jobs", t) for t in ("source_count", "skill_day", "startup"))
        if has_counts:
            counted = upsert(cur, "jobs.source_count", SOURCE_COUNT, ("source",), counts)
        else:
            print("WARNING publish: skipped: jobs.source_count not on Neon yet")
        if not has_days:
            print("WARNING publish: skipped: jobs.skill_day not on Neon yet")
        elif days:
            dayed = upsert(cur, "jobs.skill_day", SKILL_DAY, ("day", "skill"), days)
        else:
            print("WARNING publish: skipped: core.skill_day has 0 rows")
        if not startup_cols:
            print("WARNING publish: skipped: jobs.startup not on Neon yet")
        elif startups:
            pick = [i for i, c in enumerate(STARTUP) if c in startup_cols]
            startuped = upsert(cur, "jobs.startup", tuple(STARTUP[i] for i in pick), ("domain",),
                               [tuple(row[i] for i in pick) for row in startups])
        else:
            print("WARNING publish: skipped: core.startup has 0 rows")
        email = os.environ.get("OWNER_EMAIL", "").strip()
        owner = cur.execute("SELECT user_id FROM app.app_user WHERE is_owner").fetchone()
        if not owner and email:
            owner = cur.execute("SELECT user_id FROM app.app_user WHERE lower(email) = lower(%s) ORDER BY created_at LIMIT 1", (email,)).fetchone()
        if owner:
            graded = upsert(cur, "app.job_grade", GRADE, ("user_id", "job_id"),
                            [(owner[0], *g) for g in grades])
        else:
            graded = 0
            print("WARNING publish: grades skipped: no app.app_user row with is_owner and no OWNER_EMAIL match")
        deleted = job_skills_deleted = skills_deleted = counts_deleted = 0
        if not prune:
            print("WARNING publish: prunes skipped: PUBLISH_PRUNE=0")
        else:
            # Each table is checked only if Neon has it yet (remove this once radar-db's inbox_job and outreach are applied everywhere).
            held = [t for (t,) in cur.execute("SELECT t FROM unnest(%s::text[]) AS t WHERE to_regclass('app.' || t) IS NOT NULL",
                                              (list(KEPT),)).fetchall()]
            cur.execute("DELETE FROM jobs.job j WHERE j.job_id <> ALL(%s) AND j.source NOT LIKE 'email:%%'"
                        + "".join(f" AND NOT EXISTS (SELECT 1 FROM app.{t} x WHERE x.job_id = j.job_id)" for t in held), (list(ids),))
            deleted = cur.rowcount
            if has_job_skill:
                cur.execute("DELETE FROM jobs.job_skill t WHERE NOT EXISTS (SELECT 1 FROM unnest(%s::bigint[], %s::text[]) AS s(job_id, skill)"
                            " WHERE s.job_id = t.job_id AND s.skill = t.skill)", ([p[0] for p in job_skills], [p[1] for p in job_skills]))
                job_skills_deleted = cur.rowcount
            cur.execute("DELETE FROM jobs.skill WHERE skill <> ALL(%s)", ([s[0] for s in skills],))
            skills_deleted = cur.rowcount
            if has_counts:  # a source with no job in core.job any more has no count; core.job is not empty (checked above)
                cur.execute("DELETE FROM jobs.source_count WHERE source <> ALL(%s)", ([c[0] for c in counts],))
                counts_deleted = cur.rowcount
        cur.execute("SELECT app.refresh_scores()")
        # The last-publish marker the GitHub workflow's gate reads (daily-backup.yml): a collect that publishes nothing new
        # still moves it, which jobs.job.first_seen (a date) does not.
        cur.execute("INSERT INTO tracker.step_run (step, started_at, finished_at, status, summary) VALUES ('publish', %s, now(), 'ok', %s)",
                    (started, "github-actions" if os.environ.get("GITHUB_ACTIONS") else "airflow"))
    print(f"publish: {len(jobs)} jobs read, {changed} upserted (changed), {graded} grades upserted (changed), {deleted} deleted;"
          f" {len(skills)} skills read, {skilled} skills upserted (changed), {skills_deleted} skills deleted;"
          f" {len(job_skills)} job skills read, {job_skilled} job skills upserted (changed), {job_skills_deleted} job skills deleted;"
          f" {len(counts)} source counts read, {counted} source counts upserted (changed), {counts_deleted} source counts deleted;"
          f" {len(days)} skill days read, {dayed} skill days upserted (changed), 0 skill days deleted;"
          f" {len(startups)} startups read, {startuped} startups upserted (changed), 0 startups deleted")
