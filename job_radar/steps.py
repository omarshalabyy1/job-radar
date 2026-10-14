"""The pipeline steps, one Airflow task each (python -m job_radar <step>):

    schema -> extract_boards, extract_bayt, extract_remote, extract_egypt, extract_gulf, extract_workable,
              extract_freehire, extract_linkedin, extract_companies, extract_portals, extract_email (one by one)
              -> transform -> describe -> match_skills -> eligibility -> embed -> grade_claude -> ranked -> tailor;
              export_career_ops
    email_egypt, email_abroad: the two emails, right after grade_claude has ended, red or green (the DAG's `ranked`
              task carries the Asset; the HTML is in digest.py)
    track_applications: its own DAG, every hour

A source that fails is a warning and the run goes on with the others; only a crash of the step
itself (the warehouse down, the email not sent) or no network at all fails its task.
"""

from __future__ import annotations

import html
import json
import re
import socket
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from dateutil.parser import isoparse
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import sources
from .applications import cairo_today, due_now, track_applications  # noqa: F401  (track_applications: its own DAG, python -m job_radar track_applications)
from .config import (BOARD_HOSTS, DESCRIBE_SECONDS, EXTRACT_SECONDS, FEATURES, HOME, HOURS_OLD, KEEP_DAYS, NO_FETCH, OUTPUT, ROLE_LABEL, ROOT,
                     SETTINGS, hidden_reason, in_reach, is_target, place_of, role_of, too_senior)
from .digest import digest, send, subject
from .eligibility import eligibility, not_for_egypt  # noqa: F401  (python -m job_radar eligibility)
from .embed import embed  # noqa: F401  (python -m job_radar embed)
from .grade import grade_claude  # noqa: F401  (python -m job_radar grade_claude)
from .tailor import tailor  # noqa: F401  (python -m job_radar tailor)

DAYS = HOURS_OLD // 24


def schema(conn) -> None:
    """The warehouse's tables, then your companies: core.company is made the same as settings.yaml
    (companies), so a company removed there is removed here. A changed link is read and its
    platform detected again. It waits at most 10 seconds for a lock (the tracker reads the views it drops): a
    deadlock becomes a failed task Airflow retries, never a hang."""
    conn.execute("SET LOCAL lock_timeout = '10s'")  # until the commit below
    conn.execute((ROOT / "sql" / "schema.sql").read_text(encoding="utf-8"))
    companies = SETTINGS.get("companies") or []  # an empty "companies:" is no companies
    conn.execute("DELETE FROM core.company WHERE company <> ALL(%s)", ([c["name"] for c in companies],))
    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO core.company AS c (company, careers_url, starred) VALUES (%(name)s, %(link)s, %(starred)s)
            ON CONFLICT (company) DO UPDATE SET
                careers_url = EXCLUDED.careers_url, starred = EXCLUDED.starred,
                platform   = CASE WHEN c.careers_url IS DISTINCT FROM EXCLUDED.careers_url THEN NULL ELSE c.platform END,
                api        = CASE WHEN c.careers_url IS DISTINCT FROM EXCLUDED.careers_url THEN NULL ELSE c.api END,
                note       = CASE WHEN c.careers_url IS DISTINCT FROM EXCLUDED.careers_url THEN NULL ELSE c.note END,
                checked_at = CASE WHEN c.careers_url IS DISTINCT FROM EXCLUDED.careers_url THEN NULL ELSE c.checked_at END""",
            [{"name": c["name"], "link": c.get("link"), "starred": c.get("starred", True)} for c in companies])
    conn.commit()


def load(conn, name: str, rows: list[dict]) -> None:
    """Add one source's new postings to raw.job_posting: a posting already there (same source and
    link) is skipped, so re-running a step adds nothing twice. With features.eligibility, a posting someone living
    in Egypt cannot take (eligibility.not_for_egypt) is never stored. NUL characters, which Postgres refuses, are dropped."""
    barred = 0
    if FEATURES["eligibility"]:
        kept = [r for r in rows if not not_for_egypt(
            r["source"], r["location"] or "",
            r["searched_for"] or place_of(r["location"] or "") or place_of(r["title"] or "") or "Unknown location", r["payload"])]
        barred, rows_in = len(rows) - len(kept), len(rows)
        rows = kept
    else:
        rows_in = len(rows)
    clean = [{**{k: v.replace("\x00", "") if isinstance(v, str) else v for k, v in r.items()},
              "payload": Jsonb(json.loads(json.dumps(r["payload"], default=str).replace("\\u0000", "")))}
             for r in rows]
    names = sorted({r["source"] for r in rows})
    count = "SELECT count(*) FROM raw.job_posting WHERE source = ANY(%s)"
    before = conn.execute(count, (names,)).fetchone()[0]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO raw.job_posting (run_date, source, searched_for, title, company, location, job_url,"
            " date_posted, description, payload) VALUES (current_date, %(source)s, %(searched_for)s, %(title)s,"
            " %(company)s, %(location)s, %(job_url)s, %(date_posted)s, %(description)s, %(payload)s)"
            " ON CONFLICT (source, job_url) DO NOTHING", clean)
    conn.commit()
    new = conn.execute(count, (names,)).fetchone()[0] - before
    print(f"{name}: {rows_in} postings, {new} new" + (f", {barred} not for Egypt dropped" if barred else ""))
    if FEATURES["eligibility"] and rows_in:
        if warning := drop_warning(conn, name, rows_in, barred):
            print(f"WARNING {warning}")


DROP_MIN = 10  # postings a day before a board's drop rate is judged


def drop_warning(conn, board: str, postings: int, dropped: int) -> str | None:
    """Record today's postings and drops of a board (core.extract_count) and say when they look like a silent failure:
    every posting dropped from a board whose last 7 days kept some (the board changed its "who can apply" field or its
    locations), or none dropped from a board whose last 7 days dropped half or more (the rule broke). A board that always
    drops everything (Remote.co: its data jobs are US and Canada only) is not a change. None when they look normal."""
    conn.execute("INSERT INTO core.extract_count (day, board, postings, dropped) VALUES (current_date, %s, %s, %s)"
                 " ON CONFLICT (day, board) DO UPDATE SET postings = EXCLUDED.postings, dropped = EXCLUDED.dropped",
                 (board, postings, dropped))
    before, before_dropped = conn.execute(
        "SELECT coalesce(sum(postings), 0), coalesce(sum(dropped), 0) FROM core.extract_count"
        " WHERE board = %s AND day BETWEEN current_date - 7 AND current_date - 1", (board,)).fetchone()
    conn.commit()
    if postings >= DROP_MIN and dropped == postings and before >= DROP_MIN and before_dropped < before:
        return f"{board}: all {postings} postings dropped as not for Egypt - has the board changed its locations?"
    if postings >= DROP_MIN and dropped == 0 and before >= DROP_MIN and before_dropped * 2 >= before:
        return (f"{board}: none of {postings} postings dropped as not for Egypt, where the last 7 days dropped "
                f"{before_dropped} of {before} - is eligibility.not_for_egypt broken?")
    return None


def online() -> bool:
    """Whether host names resolve: without that every source comes back empty."""
    for host in ("www.google.com", "www.cloudflare.com"):
        try:
            socket.getaddrinfo(host, 443)
            return True
        except OSError:
            pass
    return False


def extract(conn, *source_functions) -> None:
    if not online():  # a red task Airflow retries, not a green run that found nothing
        raise RuntimeError("no network: host names do not resolve")
    for source in source_functions:
        try:
            rows = source()
        except Exception as e:  # one source down is a short day, not a failed run
            print(f"WARNING {source.__name__}: {e!r}"[:300])
            continue
        load(conn, source.__name__, rows)


# One extract step per group of sources: Airflow runs them side by side.
def extract_boards(conn) -> None:
    extract(conn, sources.indeed, sources.jooble)


def extract_bayt(conn) -> None:
    extract(conn, sources.bayt)


def extract_remote(conn) -> None:
    seen = {url for (url,) in conn.execute("SELECT job_url FROM raw.job_posting WHERE source = 'remoteco'")}

    def remoteco():
        return sources.remoteco(seen)

    extract(conn, sources.himalayas, sources.weworkremotely, sources.relomote, sources.remotive, sources.remoteok,
            sources.jobicy, sources.workingnomads, sources.arbeitnow, sources.dailyremote, remoteco)


def extract_startups(conn) -> None:
    extract(conn, sources.welcometothejungle, sources.startup_jobs, sources.builtin, sources.ycombinator,
            sources.wellfound)  # the slowest last: it reads what the time budget leaves


def extract_egypt(conn) -> None:
    extract(conn, sources.wuzzuf, sources.tanqeeb)


def extract_gulf(conn) -> None:
    extract(conn, sources.naukrigulf, sources.gulftalent, sources.dubizzle)


def extract_workable(conn) -> None:
    extract(conn, sources.workable_jobs)


def extract_freehire(conn) -> None:
    extract(conn, sources.freehire)


def extract_linkedin(conn) -> None:
    extract(conn, sources.linkedin)


def extract_companies(conn) -> None:
    """The careers pages in core.company; a site not yet known is detected first, and one another
    source covers is not read. A site checked in the last 24 hours is skipped, so each run moves on
    to the others, never checked or oldest checked first."""
    sites = conn.execute(
        "SELECT company, careers_url, platform, api FROM core.company WHERE coalesce(careers_url, '') <> ''"
        " AND platform IS DISTINCT FROM 'covered'"
        " AND (checked_at IS NULL OR checked_at < now() - interval '1 day')"
        " ORDER BY checked_at NULLS FIRST").fetchall()
    # 50 sites at a time, each batch saved before the next: a run stopped part way keeps what it found,
    # and no batch starts once the step's time budget is nearly spent
    for start in range(0, len(sites), 50):
        if sources.time_left(EXTRACT_SECONDS) < 70:
            print(f"extract_companies: time budget reached, {len(sites) - start} sites wait for the next run")
            break
        found: list[tuple] = []

        def company_sites():
            rows, detected = sources.company_sites(sites[start:start + 50])
            found.extend(detected)
            return rows

        extract(conn, company_sites)
        with conn.cursor() as cur:
            cur.executemany("UPDATE core.company SET platform = %s, api = %s, note = %s, checked_at = now()"
                            " WHERE company = %s", found)
        conn.commit()


def extract_portals(conn) -> None:
    extract(conn, sources.company_portals)


def extract_email(conn) -> None:
    """The jobs in your mailboxes. An email read is not downloaded again: one with jobs is known by its raw
    postings, one with none (a newsletter) by core.email_seen, kept 7 days."""
    conn.execute("DELETE FROM core.email_seen WHERE seen_at < now() - interval '7 days'")
    seen = {r[0] for r in conn.execute(
        "SELECT payload->>'message_id' FROM raw.job_posting WHERE source = 'email'"
        " UNION SELECT message_id FROM core.email_seen")}
    empty: set[str] = set()

    def mailboxes():
        return sources.mailboxes(seen, empty)

    extract(conn, mailboxes)  # commits the postings first
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO core.email_seen (message_id) VALUES (%s) ON CONFLICT DO NOTHING",
                        [(message_id,) for message_id in empty])
    conn.commit()
    print(f"extract_email: {len(empty)} emails without a job remembered")


# where a posting's own time is, by source: an ISO time, or Unix seconds or milliseconds
POSTED_KEYS = ("posted_at", "publishedAt", "pubDate", "pub_date", "publication_date", "first_published", "postedDate",
               "posted_date_ts", "LatestPostedDate", "created", "createdAt", "created_at", "first_seen", "liveStartAt",
               "published_at")


def posted_at(payload: dict, date_posted, loaded_at: datetime) -> datetime:
    """When the posting went up: its source's own time when it gives one; else its posting date when
    that is before the day the radar captured it (Indeed and Bayt give only a date); else the time the
    radar captured it, which a daily collect puts within a day of the posting."""
    for key in POSTED_KEYS:
        value = payload.get(key)
        try:
            if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
                n = float(value)
                return datetime.fromtimestamp(n / 1000 if n > 1e11 else n, timezone.utc)
            if isinstance(value, str) and "T" in value:  # a date alone ("2024-03-12") has no time: next key
                t = isoparse(value)
                return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
        except (ValueError, OverflowError, OSError):
            continue
    if date_posted and date_posted < loaded_at.date():
        return datetime.combine(date_posted, datetime.min.time(), timezone.utc)
    return loaded_at


def company_size(payload: dict) -> str | None:
    """The company's size in employees when the source gives one, written one way: Indeed's
    '51 to 200' and freehire's '51-200' both read '51-200', '1000+' reads '1,000+'."""
    enrichment = payload.get("enrichment")
    size = (payload.get("company_num_employees") or payload.get("company_size")
            or (enrichment.get("company_size") if isinstance(enrichment, dict) else None))
    if not isinstance(size, str) or not size.strip() or size.startswith("Decline"):  # Indeed's "Decline to state"
        return None
    return re.sub(r"(\d)(\d{3})\b", r"\1,\2", re.sub(r"\s+to\s+", "-", size.strip()))


def apply_url(source: str, payload: dict) -> str | None:
    """The employer's or original board's own page for a job, when the source's payload gives one (Indeed's
    job_url_direct, freehire's url when its own source is not himalayas or adzuna): else None, and the job's
    job_url stays the link."""
    url = payload.get("job_url_direct") if source == "indeed" else payload.get("url") if source == "freehire" else None
    return url if isinstance(url, str) and url.startswith("http") and not any(h in url for h in BOARD_HOSTS) else None


def job_key(title: str, company: str, job_url: str) -> str:
    """One key per job however the boards word it: the title without its bracketed notes and its
    trailing ' - City' / ' | Remote' / ' / ...' part, the company without its legal suffix, both
    lower case without punctuation, the company without its spaces and "and" too ('Care Access' and 'careaccess',
    'Robots & Pencils' and 'Robots and Pencils' are one).
    'Data Engineer - Cairo (Hybrid)' at 'Acme Ltd.' and 'Data Engineer' at 'ACME' are one job. A job with no
    company is keyed by its link."""
    def words(s: str) -> str:
        return " ".join(re.sub(r"[^\w]+", " ", s).split())
    title = re.split(r"\s+[-–—|/@]\s+", re.sub(r"\(.*?\)|\[.*?\]", " ", title.lower()))[0]
    company = re.sub(r"\b(and|llc|ltd|limited|inc|corp|corporation|co|company|plc|gmbh|s\.?a\.?e|fze|fzco)\b", " ",
                     company.lower())
    return f"{words(title)} | {words(company).replace(' ', '') or job_url}"


def archive_and_delete(cur, where: str, args: tuple, why: str) -> int:
    """Copy the jobs matching `where` (on core.job j) to core.job_archive with Claude's grade, then delete them from
    core.job; returns how many. A job already archived keeps its first row."""
    cur.execute(
        "INSERT INTO core.job_archive (job_id, job_key, title, company, location, place, source, job_url, role, date_posted,"
        " posted_at, description, company_size, first_seen, score, reason, why)"
        " SELECT j.job_id, j.job_key, j.title, j.company, j.location, j.place, j.source, j.job_url, j.role, j.date_posted,"
        " j.posted_at, j.description, j.company_size, j.first_seen, g.score, g.reason, %s"
        f" FROM core.job j LEFT JOIN core.claude_grade g USING (job_id) WHERE {where} ON CONFLICT (job_id) DO NOTHING",
        (why, *args))
    cur.execute(f"DELETE FROM core.job j WHERE {where}", args)
    return cur.rowcount


def transform(conn) -> None:
    """raw -> core.job: the window's postings with a role, not above senior, in a place in scope and
    in reach (remote, or onsite/hybrid in Cairo or Giza), one row per job (job_key: the same job on
    several boards, or reposted, is one job). A posting someone in Egypt cannot take never reaches raw.job_posting
    (load drops it); one only its description bars is stored, and the eligibility step sets its restriction and the
    email and the tracker hide it (config.hidden_reason). A job of the
    window not emailed yet that no longer passes the rules (they changed) is removed, and so is a job first seen over
    KEEP_DAYS ago (with the raw postings of then) unless you noted or applied to it; each removed job is copied to
    core.job_archive first. With no posting in the window at
    all it fails before deleting anything (every extract failed, or the raw table was emptied). Re-running it
    changes nothing."""
    postings = conn.cursor(row_factory=dict_row).execute(
        "SELECT run_date, source, searched_for, title, company, location, job_url, date_posted, description,"
        " payload->>'is_remote' = 'true' AS is_remote, payload->>'from' AS sender, payload, loaded_at"
        " FROM raw.job_posting WHERE run_date >= current_date - %s"
        " ORDER BY posting_id", (DAYS,)).fetchall()
    if not postings:  # nothing to rank: going on would delete every job of the window that was not emailed yet
        raise RuntimeError(f"transform: 0 raw postings in the last {DAYS} day(s): nothing to rank, nothing deleted")
    # your companies: a name of 4+ letters as a whole word ("Vodafone Egypt" is Vodafone); a shorter
    # one only as the whole company name, or "ag" and "db" would star "Siemens AG" and "DB Schenker"
    def letters(name: str) -> str:
        return re.sub(r"[^a-z0-9]", "", (name or "").lower())
    names = [name for (name,) in conn.execute("SELECT company FROM core.company WHERE starred")]
    short = {letters(n) for n in names if len(letters(n)) <= 3}
    yours = re.compile("|".join(rf"\b{re.escape(n)}\b" for n in names if len(letters(n)) > 3) or r"(?!)", re.I)
    # a job whose own posting has no text takes the text of the same job_key from another source, from any posting
    # still kept (KEEP_DAYS): the Indeed page of 3 days ago describes the same job's LinkedIn alert of today
    described = {job_key(title, company, job_url): description for title, company, job_url, description in conn.execute(
        "SELECT title, company, job_url, description FROM raw.job_posting WHERE run_date >= current_date - %s"
        " AND description <> '' AND title <> '' AND company <> '' ORDER BY posting_id", (KEEP_DAYS,))}
    jobs: dict[str, dict] = {}
    skipped: Counter = Counter()  # postings left out, by the first rule that left them out

    def left_out(p: dict, rank: int | None, place: str) -> str | None:
        """Why this posting is not a job in scope (the first rule it fails), or None."""
        if not (rank and place and p["job_url"]):
            return "no role"
        if too_senior(p["title"]):
            return "too senior"
        if not in_reach(place, p["location"] or "", p["title"], bool(p["is_remote"]), p["description"] or ""):
            return "not in reach"
        return None

    for p in postings:
        rank = role_of(p["title"] or "", alert=p["source"] == "email" and "linkedin.com/jobs/view/" in (p["job_url"] or "")
                       and re.search(sources.LINKEDIN_ALERTS, p["sender"] or "", re.I) is not None)
        # the place searched, else the location's, else the title's ("Data Engineer - Cairo"); a job
        # whose place none of them gives (only job alerts get this far) is "Unknown location"
        place = p["searched_for"] or place_of(p["location"] or "") or place_of(p["title"] or "") or "Unknown location"
        if reason := left_out(p, rank, place):
            skipped[reason] += 1
            continue
        key = job_key(p["title"], p["company"], p["job_url"])
        job = jobs.setdefault(key, {
            "key": key, "title": p["title"].strip(), "company": p["company"], "location": p["location"],
            "place": place, "source": p["source"], "job_url": p["job_url"], "rank": rank,
            "role": ROLE_LABEL[rank], "date_posted": p["date_posted"], "apply_url": apply_url(p["source"], p["payload"]),
            "target": (is_target(p["company"]) or yours.search(p["company"]) is not None
                       or letters(p["company"]) in short),
            "description": p["description"], "first_seen": p["run_date"],
            "posted_at": posted_at(p["payload"], p["date_posted"], p["loaded_at"])})
        job["description"] = job["description"] or p["description"] or described.get(key)
        job["apply_url"] = job["apply_url"] or apply_url(p["source"], p["payload"])
        if place == "Remote" and job["place"] != "Remote":  # found by a remote search too: a remote
            # job, shown with that remote listing's location and link
            job.update(place="Remote", location=p["location"], source=p["source"], job_url=p["job_url"])
    # a company's size from any of its postings that gives one, so its jobs from other sources show it too
    sizes: dict[str, str] = {}
    for p in postings:
        if letters(p["company"]) and (size := company_size(p["payload"])):
            sizes.setdefault(letters(p["company"]), size)
    for job in jobs.values():
        job["company_size"] = sizes.get(letters(job["company"]))
    deleted = {key for (key,) in conn.execute("SELECT job_key FROM core.job_seen")}  # never stored again
    jobs = {key: job for key, job in jobs.items() if key not in deleted}
    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO core.job AS j (job_key, title, company, location, place, source, job_url, role_rank, role,
                                       target_company, date_posted, description, first_seen, posted_at, company_size, apply_url)
            VALUES (%(key)s, %(title)s, %(company)s, %(location)s, %(place)s, %(source)s, %(job_url)s, %(rank)s,
                    %(role)s, %(target)s, %(date_posted)s, %(description)s, %(first_seen)s, %(posted_at)s,
                    %(company_size)s, %(apply_url)s)
            ON CONFLICT (job_key) DO UPDATE SET
                apply_url = coalesce(j.apply_url, EXCLUDED.apply_url),
                posted_at = coalesce(j.posted_at, EXCLUDED.posted_at),
                company_size = coalesce(EXCLUDED.company_size, j.company_size),
                description = coalesce(j.description, EXCLUDED.description),
                target_company = j.target_company OR EXCLUDED.target_company,
                place = CASE WHEN EXCLUDED.place = 'Remote' THEN 'Remote' ELSE j.place END,
                location = CASE WHEN EXCLUDED.place = 'Remote' THEN EXCLUDED.location ELSE j.location END,
                source = CASE WHEN EXCLUDED.place = 'Remote' THEN EXCLUDED.source ELSE j.source END,
                job_url = CASE WHEN EXCLUDED.place = 'Remote' THEN EXCLUDED.job_url ELSE j.job_url END""",
            list(jobs.values()))
        # the slim history first, so a job deleted below keeps its line (seen today: a posting of it came in this window)
        cur.execute(
            "INSERT INTO core.job_history AS h (job_key, first_seen, last_seen, source, role, place, score, closed_on)"
            " SELECT j.job_key, j.first_seen, CASE WHEN j.job_key = ANY(%s) THEN current_date ELSE j.first_seen END,"
            " j.source, j.role, j.place, g.score, CASE WHEN j.restriction LIKE 'no longer open%%' THEN current_date END"
            " FROM core.job j LEFT JOIN core.claude_grade g USING (job_id)"
            " ON CONFLICT (job_key) DO UPDATE SET last_seen = greatest(h.last_seen, EXCLUDED.last_seen),"
            " score = coalesce(EXCLUDED.score, h.score), place = EXCLUDED.place, closed_on = coalesce(h.closed_on, EXCLUDED.closed_on)",
            (list(jobs),))
        dropped = archive_and_delete(
            cur, "first_seen >= current_date - %s AND emailed_at IS NULL AND job_key <> ALL(%s)"
                 " AND NOT EXISTS (SELECT 1 FROM core.application a WHERE a.job_id = j.job_id)",
            (DAYS, list(jobs)), "out of scope")
        old = ("first_seen < current_date - %s"
               " AND NOT EXISTS (SELECT 1 FROM core.application a WHERE a.job_id = j.job_id)")
        cur.execute(f"INSERT INTO core.job_seen SELECT job_key FROM core.job j WHERE {old} ON CONFLICT DO NOTHING", (KEEP_DAYS,))
        aged = archive_and_delete(cur, old, (KEEP_DAYS,), "aged")
        cur.execute("DELETE FROM raw.job_posting WHERE run_date < current_date - %s", (KEEP_DAYS,))
    conn.commit()
    print(f"transform: {len(postings)} postings -> {len(jobs)} jobs in scope, {dropped} no longer in scope removed,"
          f" {aged} first seen over {KEEP_DAYS} days ago deleted; postings left out: "
          + ", ".join(f"{skipped[reason]} {reason}" for reason in ("no role", "too senior", "not in reach")))


# a job page that says the job is gone: Greenhouse sends a closed job to its board with ?error=true; others say it in words
CLOSED_PAGE = re.compile(r"no longer (open|available|accepting)|(job|position|role|vacancy|posting) (has (expired|been filled)|is closed)"
                         r"|this job (has expired|is not available)|we can.t find this page|page (can.t|cannot) be found", re.I)


def closed_reason(url: str, text: str) -> str | None:
    """Why a job page shows the job is closed (the address it ended at, or its own words), else None. Only the
    top of the page is read: a job board's footer or menu can mention expired jobs on any page."""
    if "greenhouse.io" in url and "error=true" in url:
        return "no longer open"
    found = CLOSED_PAGE.search(re.sub(r"<[^>]+>", " ", text[:60000]))
    return found.group(0).lower() if found else None


def describe(conn) -> None:
    """Read the page of each new job that came without a description, for its schema.org
    JobPosting (Greenhouse, Lever, Workday, Workable ...); never a NO_FETCH page. Sites side by
    side, each site's pages 1 second apart; a site that answers 429 is left alone for as long as it
    asks (sources.fetch), and every page left when the step's 60 seconds are up waits for the next run. A page that
    says the job is closed (closed_reason), or is gone (404, 410), hides the job like a restriction does."""
    todo = conn.execute(
        "SELECT job_id, job_url FROM core.job WHERE description IS NULL AND described_at IS NULL"
        " AND job_url LIKE 'http%%' AND job_url !~ %s AND first_seen >= current_date - %s"
        " ORDER BY role_rank, job_id LIMIT 300", (NO_FETCH, DAYS)).fetchall()
    by_site: dict[str, list] = defaultdict(list)
    closed: list[tuple] = []  # (why, job_id)
    for job_id, url in todo:
        by_site[urlsplit(url).netloc].append((job_id, url))

    def read_site(jobs: list) -> list[tuple]:
        read = []
        for job_id, url in jobs:
            if sources.time_left(DESCRIBE_SECONDS) < 10:  # room left for one page and the save
                break
            time.sleep(1)
            try:
                r = sources.fetch("GET", url, hold_on_403=False,
                                  timeout=max(5, min(30, sources.time_left(DESCRIBE_SECONDS) - 5)))
            except sources.Held:  # the site asked us to wait, or refused: none of its pages this run
                break
            except sources.ERRORS as e:
                if getattr(getattr(e, "response", None), "status_code", None) in (404, 410):
                    closed.append(("page gone", job_id))
                    read.append((None, job_id))  # looked at: not asked again
                continue  # any other error: not read, tried again next run
            if why := closed_reason(r.url, r.text):
                closed.append((why, job_id))
            read.append((page_description(r.content), job_id))  # bytes: the page's own charset decides
        return read

    with ThreadPoolExecutor(8) as pool:
        read = [x for site in pool.map(read_site, by_site.values()) for x in site]
    with conn.cursor() as cur:
        cur.executemany("UPDATE core.job SET description = %s, described_at = now() WHERE job_id = %s", read)
        cur.executemany("UPDATE core.job SET restriction = 'no longer open: ' || %s WHERE job_id = %s AND restriction IS NULL", closed)
    conn.commit()
    found = sum(text is not None for text, _ in read)
    if len(todo) >= 10 and found * 10 < len(todo):  # a site that hides its text wastes the step every run
        print(f"WARNING describe: only {found} of {len(todo)} pages gave a description - a site may hide it (config.NO_FETCH)")
    print(f"describe: {found} of {len(todo)} descriptions found" + (f", {len(closed)} jobs closed (hidden)" if closed else ""))


# "4+ years of experience", "3-5 yrs of hands-on PySpark experience", "experience: 2 years", "minimum 3 years' experience"
YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*\d{1,2}\s*)?\+?\s*(?:years?|yrs?)['’]?\s*(?:of\s+)?(?:[\w/&.,+#-]+\s+){0,5}?experience"
                   r"|experience\s*(?:of|:)?\s*(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*\d{1,2}\s*)?(?:years?|yrs?)", re.I)


def years_required(text: str | None) -> int | None:
    """The most years of experience a posting asks for, by its lowest bound: "4+ years of experience ... 3+ years of
    PySpark experience" is 4. None when it names none in digits (grade_claude then asks Claude)."""
    found = [int(a or b) for a, b in YEARS.findall(text or "")]
    found = [n for n in found if 0 < n <= 20]
    return max(found) if found else None


def match_skills(conn) -> None:
    """Store which of your skills each job of the window asks for (core.job_skill), so the email
    and the tracker read a small table instead of matching every job's text each time. Re-running it gives the
    same rows."""
    window = "SELECT job_id FROM core.job WHERE first_seen >= current_date - %s"
    conn.execute(f"DELETE FROM core.job_skill WHERE job_id IN ({window})", (DAYS,))
    matched = conn.execute(
        "INSERT INTO core.job_skill (job_id, skill) SELECT j.job_id, s.skill FROM core.job j"
        " JOIN core.skill s ON j.title || ' ' || coalesce(j.description, '') ~* s.pattern"
        f" WHERE j.job_id IN ({window})", (DAYS,)).rowcount
    conn.execute("DELETE FROM core.cv_skill")  # your CVs: few and small, matched again every run
    conn.execute("INSERT INTO core.cv_skill (label, skill) SELECT c.label, s.skill FROM core.cv c"
                 " JOIN core.skill s ON c.text ~* s.pattern")
    asked = [(years_required(description), job_id) for job_id, description in conn.execute(
        "SELECT job_id, description FROM core.job WHERE first_seen >= current_date - %s AND description IS NOT NULL"
        " AND years_required IS NULL", (KEEP_DAYS,))]
    with conn.cursor() as cur:
        cur.executemany("UPDATE core.job SET years_required = %s WHERE job_id = %s", [a for a in asked if a[0]])
    conn.commit()
    print(f"match_skills: {matched} skill matches, years asked found in {sum(1 for a in asked if a[0])} of {len(asked)} descriptions")


def export_career_ops(conn) -> None:
    """Hand the window's best matches to career-ops (github.com/career-ops-hq/career-ops), which
    scores them against your CV and tailors it when you run it in Claude Code: writes
    output/career-ops/pipeline.md (one job link per line, best first) and cv.md (your newest CV).
    Copy both into your career-ops folder (data/pipeline.md, cv.md). A job you ignored or rejected is left out, and so
    is one config.hidden_reason hides (as the switches stand): the 25 are the best of the rest."""
    jobs = [(url, title, company, place, skills) for url, title, company, place, skills, *hide in conn.execute(
        "SELECT job_url, title, company, place, skill_matches, status, restriction, score FROM mart.job_status"
        " WHERE first_seen >= current_date - %s AND skill_matches >= 3 AND status NOT IN ('ignored', 'rejected')"
        " ORDER BY role_rank, target_company DESC, skill_matches DESC", (DAYS,)) if not hidden_reason(*hide)][:25]
    folder = OUTPUT / "career-ops"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "pipeline.md").write_text(
        "# Pipeline\n\n" + "".join(f"- [ ] {url} | {company} | {title} | {place} | {skills} of your skills\n"
                                   for url, title, company, place, skills in jobs), encoding="utf-8")
    cv = conn.execute("SELECT text FROM core.cv ORDER BY uploaded_at DESC LIMIT 1").fetchone()
    if cv:
        (folder / "cv.md").write_text(cv[0], encoding="utf-8")
    print(f"export_career_ops: {len(jobs)} jobs{' and your CV' if cv else ''} in {folder}")


def page_description(page: bytes) -> str | None:
    soup = BeautifulSoup(page, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "JobPosting" and item.get("description"):
                return BeautifulSoup(html.unescape(item["description"]), "html.parser").get_text(" ", strip=True)
    return None


def email_egypt(conn) -> None:
    """The email of your home's jobs."""
    email(conn, home=True)


def email_abroad(conn) -> None:
    """The email of the jobs everywhere else: remote only, no hybrid or onsite."""
    email(conn, home=False)


# The window's jobs of one side (home or not) not emailed yet, best first; Claude's score counts only with features.grade
EMAIL_JOBS = """
    SELECT s.*, j.description, r.payload->>'job_type' AS job_type FROM mart.job_status s
    JOIN core.job j USING (job_id) LEFT JOIN raw.job_posting r ON (r.source, r.job_url) = (s.source, s.job_url)
    WHERE s.emailed_at IS NULL AND s.first_seen >= current_date - %s AND (s.place = %s) = %s
      AND s.status NOT IN ('ignored', 'rejected')
    ORDER BY s.role_rank, s.target_company DESC, CASE WHEN %s THEN s.score END DESC NULLS LAST, s.skill_matches DESC,
             s.date_posted DESC NULLS LAST"""


def email(conn, home: bool) -> None:
    """Email the window's jobs at home (or outside it) not emailed yet, by place, experience, role
    and employment type, best first in each: target companies, then Claude's score, then how many of
    your skills they ask for; nothing when there is no new job and no follow-up (or when the follow-ups alone were already sent today). A job you ignored or rejected is left out,
    and so is one config.hidden_reason hides (a restriction, a score of 3 or lower, as the switches stand).
    The home email also lists the oldest 10 applications to follow up (applications.due_follow_ups: the same rule and the same
    company matching as the follow-up drafts), and is sent on a day with no new job when one is due: its subject says follow-ups.
    Each job is marked emailed, so it is never sent twice. Without Gmail settings, write it to output/
    instead and mark nothing."""
    name = HOME if home else f"Outside {HOME}"
    jobs = conn.cursor(row_factory=dict_row).execute(EMAIL_JOBS, (DAYS, HOME, home, FEATURES["grade"])).fetchall()
    jobs = [j for j in jobs if not hidden_reason(j["status"], j["restriction"], j["score"])]
    if not FEATURES["grade"]:  # off: no score or reason in the email either
        for j in jobs:
            j["score"] = j["grade_reason"] = None
    follow_ups = [{"company": company, "title": title, "applied_at": day}
                  for company, title, day, _ in due_now(conn)[:10]] if home and FEATURES["follow_up"] else []
    sent_marker = OUTPUT / f"followups-emailed-{cairo_today()}"  # the Cairo day the follow-ups went out: a second collect that day has none to add
    if not jobs and (not follow_ups or sent_marker.exists()):
        print(f"email {name}: no new jobs, nothing sent")
        return
    if send(digest(jobs, name, follow_ups), subject(jobs, name, follow_ups), name):
        conn.execute("UPDATE core.job SET emailed_at = now() WHERE job_id = ANY(%s)", ([j["job_id"] for j in jobs],))
        conn.commit()
        if follow_ups:
            sent_marker.parent.mkdir(parents=True, exist_ok=True)
            sent_marker.touch()
