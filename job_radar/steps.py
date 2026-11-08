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
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from dateutil.parser import isoparse
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import fx, runlog, sources
from .applications import cairo_today, due_now, track_applications  # noqa: F401  (track_applications: its own DAG, python -m job_radar track_applications)
from .config import (BOARD_HOSTS, CLOSED_AFTER_DAYS, DESCRIBE_SECONDS, EXTRACT_SECONDS, FEATURES, HOME, HOURS_OLD, KEEP_DAYS, NO_FETCH, OUTPUT,
                     ROLE_LABEL, ROOT, SEEN_DAYS, WORK_DAYS,
                     NOT_REMOTE, SETTINGS, hidden_reason, in_reach, is_target, place_of, reasons, role_of, too_senior)
from .digest import digest, send, subject
from .eligibility import eligibility, not_for_egypt  # noqa: F401  (python -m job_radar eligibility)
from .embed import embed  # noqa: F401  (python -m job_radar embed)
from .grade import grade_claude  # noqa: F401  (python -m job_radar grade_claude)
from .outreach import outreach_drafts  # noqa: F401  (python -m job_radar outreach_drafts)
from .publish import publish  # noqa: F401  (python -m job_radar publish)
from .publish_applications import publish_applications  # noqa: F401  (python -m job_radar publish_applications)
from .tech import is_tech_role
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


def load(conn, name: str, rows: list[dict], wide: bool = False, record: bool = True) -> tuple[int, int]:
    """Add one source's new postings to raw.job_posting: a posting already there (same source and
    link) only has its last_seen moved to today, so re-running a step adds nothing twice. With features.eligibility, a
    posting someone living in Egypt cannot take (eligibility.not_for_egypt) is never stored, unless the source is `wide`
    (the website's collection: transform then keeps it out of your scope). NUL characters, which Postgres refuses, are dropped.
    Returns (postings, dropped); without `record` they are not recorded (drop_warning): the caller counts its batches once."""
    barred = 0
    if FEATURES["eligibility"] and not wide:
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
            "INSERT INTO raw.job_posting AS r (run_date, last_seen, source, searched_for, title, company, location, job_url,"
            " date_posted, description, payload) VALUES (current_date, current_date, %(source)s, %(searched_for)s, %(title)s,"
            " %(company)s, %(location)s, %(job_url)s, %(date_posted)s, %(description)s, %(payload)s)"
            " ON CONFLICT (source, job_url) DO UPDATE SET last_seen = current_date"
            " WHERE r.last_seen IS DISTINCT FROM current_date", clean)
    conn.commit()
    new = conn.execute(count, (names,)).fetchone()[0] - before
    print(f"{name}: {rows_in} postings, {new} new" + (f", {barred} not for Egypt dropped" if barred else ""))
    if record and (warning := drop_warning(conn, name, rows_in, barred, judged=FEATURES["eligibility"] and not wide)):
        print(f"WARNING {warning}")
    return rows_in, barred


DROP_MIN = 10  # postings a day before a board's drop rate is judged
SILENT_DAYS = 7  # days in a row with no posting at all, since its last, before a board is called dead
# Boards that give no posting by design, so their zero is not a dead board: Remote.co's data jobs are US and Canada only
# (it drops everything it reads), and Dubizzle and NaukriGulf are Gulf boards, whose jobs are onsite or for a Gulf resident.
# Remotive and Working Nomads work, but their public feeds are tiny (13 and 17 jobs, checked 2026-11-08) and rarely hold a
# role of yours: Omar chose not to watch them (2026-11-08). Their "0 postings today, N in the last 7 days" warning stays.
# So do Arbeitnow (Europe: 15 remote jobs of its 300, none yours) and We Work Remotely (1 posting of its feed in scope),
# also Omar's choice (2026-11-08).
# Relomote and startup.jobs answered 403 (held 6 hours, sources/base.py) when checked 2026-11-08: Omar chose not to watch them
# either. Each refusal still prints its own "WARNING <host> answered 403: held" line every run, so a block is not silent.
EMPTY_BY_DESIGN = {"remoteco", "dubizzle", "naukrigulf", "remotive", "workingnomads", "arbeitnow", "weworkremotely",
                   "relomote", "startup_jobs"}


def drop_warning(conn, board: str, postings: int, dropped: int, judged: bool = True) -> str | None:
    """Record today's postings and drops of a board (core.extract_count) and say when they look like a silent failure:
    no posting at all from a board whose last 7 days gave some (the board changed, or blocks us); and, for a board whose
    postings are judged for Egypt (`judged`), every posting dropped from a board whose last 7 days kept at least a tenth (the
    board changed its "who can apply" field or its locations; a global board like Freehire keeps about 2%, so 100% is
    normal), or none dropped from a board whose last 7 days dropped half or more (the rule broke). A board that always drops everything (Remote.co: its data jobs are US and Canada only) is not a
    change. A board that has also given none for SILENT_DAYS days in a row (0 today and every recorded day since its last posting,
    7 or more) is a warning every day until it gives one: dead - changed, blocked, held or without its key - not a quiet week.
    Not for EMPTY_BY_DESIGN, nor for a company's own board (`ats:token`: most have no job in scope on most days). None when
    they look normal."""
    conn.execute("INSERT INTO core.extract_count (day, board, postings, dropped) VALUES (current_date, %s, %s, %s)"
                 " ON CONFLICT (day, board) DO UPDATE SET postings = EXCLUDED.postings, dropped = EXCLUDED.dropped",
                 (board, postings, dropped))
    before, before_dropped = conn.execute(
        "SELECT coalesce(sum(postings), 0), coalesce(sum(dropped), 0) FROM core.extract_count"
        " WHERE board = %s AND day BETWEEN current_date - 7 AND current_date - 1", (board,)).fetchone()
    conn.commit()
    if postings == 0 and before > 0:
        return f"{board}: 0 postings today, {before} in the last 7 days - has the board changed, or does it block us?"
    if postings == 0 and board not in EMPTY_BY_DESIGN and ":" not in board:
        quiet = conn.execute(
            "SELECT count(*) FROM core.extract_count WHERE board = %s AND day > coalesce("
            "(SELECT max(day) FROM core.extract_count WHERE board = %s AND postings > 0), date '1900-01-01')",
            (board, board)).fetchone()[0]
        if quiet >= SILENT_DAYS:
            return (f"{board}: 0 postings every day for {quiet} days - is the board dead (changed, blocked or held, "
                    f"or its key gone)?")
    if not judged:
        return None
    if postings >= DROP_MIN and dropped == postings and before >= DROP_MIN and (before - before_dropped) * 10 >= before:
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
    source covers is not read. A site checked in the last 20 hours is skipped (not 24: checked_at is stamped after the step
    starts, so yesterday's read is under 24 hours old), so each run moves on to the others, never checked or oldest checked
    first. The day's count (drop_warning) is all batches', once: a last batch with no job is not a silent board."""
    if not online():  # a red task Airflow retries, not a green run that found nothing
        raise RuntimeError("no network: host names do not resolve")
    sites = conn.execute(
        "SELECT company, careers_url, platform, api FROM core.company WHERE coalesce(careers_url, '') <> ''"
        " AND platform IS DISTINCT FROM 'covered'"
        " AND (checked_at IS NULL OR checked_at < now() - interval '20 hours')"
        " ORDER BY checked_at NULLS FIRST").fetchall()
    batches = postings = dropped = 0
    # 50 sites at a time, each batch saved before the next: a run stopped part way keeps what it found,
    # and no batch starts once the step's time budget is nearly spent
    for start in range(0, len(sites), 50):
        if sources.time_left(EXTRACT_SECONDS) < 70:
            print(f"extract_companies: time budget reached, {len(sites) - start} sites wait for the next run")
            break
        try:
            rows, found = sources.company_sites(sites[start:start + 50])
        except Exception as e:  # one batch down is a short day, not a failed run
            print(f"WARNING company_sites: {e!r}"[:300])
            continue
        batch = load(conn, "company_sites", rows, record=False)
        batches, postings, dropped = batches + 1, postings + batch[0], dropped + batch[1]
        with conn.cursor() as cur:
            cur.executemany("UPDATE core.company SET platform = %s, api = %s, note = %s, checked_at = now()"
                            " WHERE company = %s", found)
        conn.commit()
    if batches and (warning := drop_warning(conn, "company_sites", postings, dropped, judged=FEATURES["eligibility"])):
        print(f"WARNING {warning}")


MARKET_BOARDS = 40  # boards read a run, oldest read first: grown step by step (Omar, 2026-10-17: 20, then 40; watch time and RAM)


def extract_market(conn) -> None:
    """The website's collection (features.market): company boards from core.board (sources/boards.csv is added first, once;
    with features.market_harvest, every board the last WORK_DAYS' postings link to is added too), each read whole at most
    once in 20 hours, inside the step's time budget: active boards due first, oldest read first, then new candidates. A
    board read becomes active; one gone (404) or failing 3 runs in a row is dead. Its tech jobs are stored as wide postings
    (load: not dropped for Egypt; transform keeps them out of your scope unless one is yours)."""
    if not FEATURES.get("market"):
        print("extract_market: off in settings.yaml (features.market)")
        return
    if not online():
        raise RuntimeError("no network: host names do not resolve")
    import csv
    seed = [r for r in csv.reader((ROOT / "job_radar" / "sources" / "boards.csv").read_text(encoding="utf-8").splitlines())
            if r and not r[0].startswith("#") and r[0] != "ats"]
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO core.board (ats, token, origin) VALUES (%s, %s, 'radarak') ON CONFLICT DO NOTHING", seed)
        if FEATURES.get("market_harvest"):  # the boards other sources' postings link to (a company's own board)
            links = conn.execute("SELECT job_url || ' ' || payload::text FROM raw.job_posting WHERE source NOT LIKE '%%-board'"
                                 " AND last_seen >= current_date - %s", (WORK_DAYS,)).fetchall()
            found = list(dict.fromkeys(board for (text,) in links for board in sources.market.boards_in(text)))
            cur.executemany("INSERT INTO core.board (ats, token, origin) VALUES (%s, %s, 'harvested') ON CONFLICT DO NOTHING", found)
            print(f"extract_market: {len(found)} boards linked from the last {WORK_DAYS} days' postings, {cur.rowcount} new")
    if FEATURES.get("market_more"):  # the remote boards' whole feeds and Germany's job agency, a minute at most
        for source in (sources.market.remote_apis, sources.market.bundesagentur):
            try:
                load(conn, source.__name__, source(), wide=True)
            except Exception as e:  # one source down is a short day
                print(f"WARNING extract_market {source.__name__}: {e!r}"[:300])
    boards = conn.execute(
        "SELECT ats, token, coalesce(company, initcap(replace(token, '-', ' '))) FROM core.board WHERE status <> 'dead'"
        " AND (checked_at IS NULL OR checked_at < now() - interval '20 hours')"
        " ORDER BY status = 'active' DESC, checked_at NULLS FIRST, ats, token LIMIT %s", (MARKET_BOARDS,)).fetchall()
    conn.commit()
    read = failed = 0
    for ats, token, company in boards:
        if sources.time_left(EXTRACT_SECONDS) < 20:
            print(f"extract_market: time budget reached, {len(boards) - read - failed} boards wait for the next run")
            break
        try:
            rows = sources.market.READERS[ats](token, company)
        except Exception as e:  # one board down is a short day; gone (404) is dead at once, else after 3 runs in a row
            failed += 1
            gone = getattr(getattr(e, "response", None), "status_code", None) == 404
            conn.execute("UPDATE core.board SET failures = failures + 1, checked_at = now(), note = %s,"
                         " status = CASE WHEN %s OR failures + 1 >= 3 THEN 'dead' ELSE status END WHERE (ats, token) = (%s, %s)",
                         (f"{e!r}"[:200], gone, ats, token))
            conn.commit()
            print(f"WARNING extract_market {ats}:{token}: {e!r}"[:300])
            continue
        read += 1
        load(conn, f"{ats}:{token}", rows, wide=True)
        conn.execute("UPDATE core.board SET status = 'active', failures = 0, jobs = %s, checked_at = now(), last_ok_at = now(),"
                     " note = NULL WHERE (ats, token) = (%s, %s)", (len(rows), ats, token))
        conn.commit()
    print(f"extract_market: {read} of {len(boards)} boards read, {failed} failed")


def extract_apify(conn) -> None:
    """The website's hard boards through Apify (sources/apify.py, settings.yaml apify): every enabled board's Actor run
    inside the month's budget, as wide postings. Off until a board is enabled."""
    if not FEATURES.get("market"):
        print("extract_apify: off in settings.yaml (features.market)")
        return
    rows = sources.apify.apify_jobs()
    for board in sorted({r["source"] for r in rows}):
        load(conn, board, [r for r in rows if r["source"] == board], wide=True)


def extract_portals(conn) -> None:
    """company_portals. Nothing back (its feed unchanged since its last read, or no chunk read: each failure prints its
    own WARNING) is not counted, so an unchanged feed is not a board gone silent (drop_warning)."""
    if not online():
        raise RuntimeError("no network: host names do not resolve")
    try:
        rows = sources.company_portals()
    except Exception as e:  # down is a short day, not a failed run
        print(f"WARNING company_portals: {e!r}"[:300])
        return
    if rows:
        load(conn, "company_portals", rows)


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


# (lowest, highest, currency, period) as each board names them: Indeed and Bayt (JobSpy), Himalayas, Jobicy
SALARY_FIELDS = [("min_amount", "max_amount", "currency", "interval"), ("minSalary", "maxSalary", "currency", "salaryPeriod"),
                 ("salaryMin", "salaryMax", "salaryCurrency", "salaryPeriod")]


def salary(payload: dict) -> dict | None:
    """The pay the board gives for the job, as given: {salary_min, salary_max, salary_currency, salary_period}, or None
    when it gives no amount above 0 or no currency. fx.py turns it into a year's pay in dollars and euros."""
    for low, high, currency, period in SALARY_FIELDS:
        amounts = []
        for field in (low, high):
            try:
                amounts.append(float(payload.get(field)) if payload.get(field) not in (None, "") else None)
            except (TypeError, ValueError):
                amounts.append(None)
        amounts = [a if a and a > 0 else None for a in amounts]
        code = payload.get(currency)
        if any(amounts) and isinstance(code, str) and re.fullmatch(r"[A-Za-z]{3}", code.strip()):
            return {"salary_min": amounts[0], "salary_max": amounts[1], "salary_currency": code.strip().upper(),
                    "salary_period": (payload.get(period) or None) if isinstance(payload.get(period), str) else None}
    return None


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


COPY_SUFFIX = re.compile(r"\s*(?:[-–—:|]\s*copy(?:\s*\d+)?|\(\s*copy(?:\s*\d+)?\s*\))\s*$", re.I)  # a recruiter's leftover
ARABIC_PLACES = {"القاهرة": "Cairo", "القاهرة الجديدة": "New Cairo", "الجيزة": "Giza", "مدينة نصر": "Nasr City",
                 "السادس من أكتوبر": "6th of October", "الإسكندرية": "Alexandria", "الرياض": "Riyadh", "جدة": "Jeddah",
                 "دبي": "Dubai", "أبو ظبي": "Abu Dhabi", "الدوحة": "Doha", "الكويت": "Kuwait", "عمان": "Amman", "مصر الجديدة": "Heliopolis",
                 "مدينة العبور": "Obour City", "المعادي": "Maadi"}
COUNTRY_CODES = {"EG": "Egypt", "SA": "Saudi Arabia", "AE": "UAE", "QA": "Qatar", "KW": "Kuwait", "BH": "Bahrain",
                 "OM": "Oman", "JO": "Jordan"}


SMALL_WORDS = {"and", "or", "for", "of", "the", "to", "in", "at", "with"}


def clean_title(title: str) -> str:
    """The title as shown: without a trailing ' - Copy' / '(copy 2)' ('Copy Editor' stays), and one in capitals in title
    case, short words kept as acronyms ('AI ENGINEER' -> 'AI Engineer', DailyRemote's). job_key reads the raw title."""
    title = COPY_SUFFIX.sub("", title.strip()) or title.strip()
    if title.isupper():
        title = " ".join(w.lower() if w.lower() in SMALL_WORDS else w if len(w) <= 3 else w.capitalize() for w in title.split())
    return title


def foreign(text: str) -> bool:
    """Has a letter outside the Latin alphabets (Arabic, ...); an emoji or a dash is not a letter."""
    return any(ch.isalpha() and ord(ch) > 0x24F for ch in text)


def clean_location(location: str, fallback: str | None = None) -> str:
    """A location in Latin letters, as shown. One with letters of another script ('القاهرة, C, EG') has its city named
    in English and its bare region codes dropped ('Cairo, Egypt'); 'Daftra · Giza (Hybrid)' keeps the part after the
    company. A part with no English name is dropped; nothing left gives the fallback (the job's place), else the
    location as it came: never None (core.job.location is NOT NULL). Latin letters are left as they are."""
    if not location:
        return fallback or "n/a"  # an alert that gave none: the job's place
    if not foreign(location):
        return location
    parts = []
    for part in (p.strip() for p in location.split("·")[-1].split(",")):
        part = ARABIC_PLACES.get(part) or COUNTRY_CODES.get(part) or part
        if part and not re.fullmatch(r"[A-Z]{1,2}", part) and not foreign(part) and part not in parts:
            parts.append(part)
    return ", ".join(parts) or fallback or location


def archive_and_delete(cur, where: str, args: tuple, why: str) -> int:
    """Copy your jobs (in_scope) matching `where` (on core.job j) to core.job_archive with Claude's grade, then delete
    every job matching it from core.job; returns how many. A job already archived keeps its first row."""
    cur.execute(
        "INSERT INTO core.job_archive (job_id, job_key, title, company, location, place, source, job_url, role, date_posted,"
        " posted_at, description, company_size, first_seen, score, reason, why)"
        " SELECT j.job_id, j.job_key, j.title, j.company, j.location, j.place, j.source, j.job_url, j.role, j.date_posted,"
        " j.posted_at, j.description, j.company_size, j.first_seen, g.score, g.reason, %s"
        f" FROM core.job j LEFT JOIN core.claude_grade g USING (job_id) WHERE j.in_scope AND {where} ON CONFLICT (job_id) DO NOTHING",
        (why, *args))
    cur.execute(f"DELETE FROM core.job j WHERE {where}", args)
    return cur.rowcount


def transform(conn) -> None:
    """raw -> core.job: the postings a source listed in the window, one row per job (job_key: the same job on several
    boards, or reposted, is one job). Yours (in_scope): a role, not above senior, in a place in scope and in reach
    (remote, or onsite/hybrid in Cairo or Giza), and not barred to someone in Egypt (eligibility.not_for_egypt). With
    features.market, every other tech job (tech.is_tech_role) is kept too, in_scope false, for the website only. One only
    its description bars is stored, and the eligibility step sets its restriction and the email and the tracker hide it
    (config.hidden_reason). A job of the window not emailed yet that no longer passes the rules (they changed) is
    removed; so is a job first seen over KEEP_DAYS ago, and a company board's job the board stopped listing
    CLOSED_AFTER_DAYS ago, unless you noted or applied to it; each removed job of yours is copied to core.job_archive
    first. Raw postings not seen for WORK_DAYS are deleted. With no posting in the window at all it fails before
    deleting anything (every extract failed, or the raw table was emptied). Re-running it changes nothing."""
    postings = conn.cursor(row_factory=dict_row).execute(
        "SELECT run_date, last_seen, source, searched_for, title, company, location, job_url, date_posted, description,"
        " payload->>'is_remote' = 'true' AS is_remote, payload->>'from' AS sender, payload, loaded_at"
        " FROM raw.job_posting WHERE last_seen >= current_date - %s"
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
    # still kept (WORK_DAYS): the Indeed page of 3 days ago describes the same job's LinkedIn alert of today
    described = {job_key(title, company, job_url): description for title, company, job_url, description in conn.execute(
        "SELECT title, company, job_url, description FROM raw.job_posting WHERE last_seen >= current_date - %s"
        " AND description <> '' AND title <> '' AND company <> '' ORDER BY posting_id", (WORK_DAYS,))}
    jobs: dict[str, dict] = {}
    skipped: Counter = Counter()  # postings out of your scope, by the first rule that left them out
    market = FEATURES.get("market", False)

    def left_out(p: dict, rank: int | None, place: str) -> str | None:
        """Why this posting is not a job in your scope (the first rule it fails), or None."""
        if not (rank and place and p["job_url"]):
            return "no role"
        if too_senior(p["title"]):
            return "too senior"
        if not in_reach(place, p["location"] or "", p["title"], bool(p["is_remote"]), p["description"] or ""):
            return "not in reach"
        if FEATURES["eligibility"] and not_for_egypt(p["source"], p["location"] or "", place, p["payload"]):
            return "barred"
        return None

    for p in postings:
        rank = role_of(p["title"] or "", alert=p["source"] == "email" and "linkedin.com/jobs/view/" in (p["job_url"] or "")
                       and re.search(sources.LINKEDIN_ALERTS, p["sender"] or "", re.I) is not None)
        # the place searched, else the location's, else the title's ("Data Engineer - Cairo"); a job
        # whose place none of them gives (only job alerts get this far) is "Unknown location"
        place = p["searched_for"] or place_of(p["location"] or "") or place_of(p["title"] or "") or "Unknown location"
        if reason := left_out(p, rank, place):
            skipped[reason] += 1
            if not (market and p["job_url"] and p["title"] and is_tech_role(p["title"])):
                continue
        key = job_key(p["title"], p["company"], p["job_url"])
        # a job not yours has rank 0 and your role's name when its title has one, else its tech role family (SQL)
        job = jobs.setdefault(key, {
            "key": key, "title": clean_title(p["title"]), "company": p["company"], "location": clean_location(p["location"], place),
            "place": place, "source": p["source"], "job_url": p["job_url"], "rank": 0 if reason else rank,
            "role": ROLE_LABEL[rank] if rank else None, "in_scope": not reason, "last_seen": p["last_seen"],
            "date_posted": p["date_posted"], "apply_url": apply_url(p["source"], p["payload"]),
            "target": (is_target(p["company"]) or yours.search(p["company"]) is not None
                       or letters(p["company"]) in short),
            # first seen: the day it becomes a job (a posting stored days ago, out of scope then, can become one today)
            "description": p["description"], "first_seen": p["last_seen"],
            "posted_at": posted_at(p["payload"], p["date_posted"], p["loaded_at"])})
        job["description"] = job["description"] or p["description"] or described.get(key)
        job["apply_url"] = job["apply_url"] or apply_url(p["source"], p["payload"])
        job["salary"] = job.get("salary") or salary(p["payload"])  # from any of its postings that gives one
        job["last_seen"] = max(job["last_seen"], p["last_seen"])
        if reason:
            continue
        if not job["in_scope"]:  # a posting of it is yours: the job is yours, with this posting's role and place
            job.update(in_scope=True, rank=rank, role=ROLE_LABEL[rank], place=place, location=clean_location(p["location"], place),
                       source=p["source"], job_url=p["job_url"])
        if place == "Remote" and job["place"] != "Remote":  # found by a remote search too: a remote
            # job, shown with that remote listing's location and link
            job.update(place="Remote", location=clean_location(p["location"], "Remote"), source=p["source"], job_url=p["job_url"])
    # a company's size from any of its postings that gives one, so its jobs from other sources show it too
    sizes: dict[str, str] = {}
    for p in postings:
        if letters(p["company"]) and (size := company_size(p["payload"])):
            sizes.setdefault(letters(p["company"]), size)
    for job in jobs.values():
        job["company_size"] = sizes.get(letters(job["company"]))
        job.update(job.pop("salary") or dict.fromkeys(("salary_min", "salary_max", "salary_currency", "salary_period")))
    # a key deleted, then deleted or posted again within SEEN_DAYS, is not stored again (one not seen longer is a new opening)
    deleted = {key for (key,) in conn.execute("SELECT job_key FROM core.job_seen WHERE seen_on >= current_date - %s",
                                               (SEEN_DAYS,))} & jobs.keys()
    jobs = {key: job for key, job in jobs.items() if key not in deleted}
    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO core.job AS j (job_key, title, company, location, place, source, job_url, role_rank, role,
                                       target_company, date_posted, description, first_seen, posted_at, company_size, apply_url,
                                       salary_min, salary_max, salary_currency, salary_period, in_scope, last_seen)
            VALUES (%(key)s, %(title)s, %(company)s, %(location)s, %(place)s, %(source)s, %(job_url)s, %(rank)s,
                    coalesce(%(role)s, core.role_family(%(title)s)), %(target)s, %(date_posted)s, %(description)s,
                    %(first_seen)s, %(posted_at)s, %(company_size)s, %(apply_url)s, %(salary_min)s, %(salary_max)s,
                    %(salary_currency)s, %(salary_period)s, %(in_scope)s, %(last_seen)s)
            ON CONFLICT (job_key) DO UPDATE SET
                in_scope = j.in_scope OR EXCLUDED.in_scope,
                first_seen = CASE WHEN EXCLUDED.in_scope AND NOT j.in_scope THEN EXCLUDED.first_seen ELSE j.first_seen END,
                role_rank = CASE WHEN EXCLUDED.in_scope AND NOT j.in_scope THEN EXCLUDED.role_rank ELSE j.role_rank END,
                role = CASE WHEN EXCLUDED.in_scope AND NOT j.in_scope THEN EXCLUDED.role ELSE j.role END,
                last_seen = greatest(j.last_seen, EXCLUDED.last_seen),
                apply_url = coalesce(j.apply_url, EXCLUDED.apply_url),
                salary_min = CASE WHEN j.salary_currency IS NULL THEN EXCLUDED.salary_min ELSE j.salary_min END,
                salary_max = CASE WHEN j.salary_currency IS NULL THEN EXCLUDED.salary_max ELSE j.salary_max END,
                salary_period = CASE WHEN j.salary_currency IS NULL THEN EXCLUDED.salary_period ELSE j.salary_period END,
                salary_currency = coalesce(j.salary_currency, EXCLUDED.salary_currency),
                posted_at = coalesce(j.posted_at, EXCLUDED.posted_at),
                company_size = coalesce(EXCLUDED.company_size, j.company_size),
                description = coalesce(j.description, EXCLUDED.description),
                target_company = j.target_company OR EXCLUDED.target_company,
                place = CASE WHEN {moved} THEN EXCLUDED.place ELSE j.place END,
                location = CASE WHEN {moved} THEN EXCLUDED.location ELSE j.location END,
                source = CASE WHEN {moved} THEN EXCLUDED.source ELSE j.source END,
                job_url = CASE WHEN {moved} THEN EXCLUDED.job_url ELSE j.job_url END""".format(
                # a remote listing of it (yours), or the first posting of it that is yours: the job takes its place and link
                moved="EXCLUDED.in_scope AND (EXCLUDED.place = 'Remote' OR NOT j.in_scope)"),
            list(jobs.values()))
        cur.execute("UPDATE core.job_seen SET seen_on = current_date WHERE job_key = ANY(%s)", (list(deleted),))  # still posted
        # the slim history first, so a job deleted below keeps its line (seen today: a posting of it came in this window)
        cur.execute(
            "INSERT INTO core.job_history AS h (job_key, first_seen, last_seen, source, role, place, score, closed_on)"
            " SELECT j.job_key, j.first_seen, CASE WHEN j.job_key = ANY(%s) THEN current_date ELSE j.first_seen END,"
            " j.source, j.role, j.place, g.score, CASE WHEN j.restriction LIKE 'no longer open%%' THEN current_date END"
            " FROM core.job j LEFT JOIN core.claude_grade g USING (job_id) WHERE j.in_scope"
            " ON CONFLICT (job_key) DO UPDATE SET last_seen = greatest(h.last_seen, EXCLUDED.last_seen),"
            " score = coalesce(EXCLUDED.score, h.score), place = EXCLUDED.place, closed_on = coalesce(h.closed_on, EXCLUDED.closed_on)",
            (list(jobs),))
        dropped = archive_and_delete(
            cur, "first_seen >= current_date - %s AND emailed_at IS NULL AND job_key <> ALL(%s)"
                 " AND NOT EXISTS (SELECT 1 FROM core.application a WHERE a.job_id = j.job_id)",
            (DAYS, list(jobs)), "out of scope")
        kept = (" AND NOT EXISTS (SELECT 1 FROM core.application a WHERE a.job_id = j.job_id)"
                # a job with a deadline not done yet and not past stays, or the deadline would lose its job
                # (mart.job_status's fresh_level 6 says the same)
                " AND NOT EXISTS (SELECT 1 FROM core.deadline d WHERE d.job_id = j.job_id AND NOT d.done AND d.due >= current_date)")
        # a board's job it has not listed for CLOSED_AFTER_DAYS although the board was read since (a board not read is no
        # sign; one whose posting is already gone from raw keeps the job until KEEP_DAYS)
        closed_where = ("j.source LIKE '%%-board' AND j.last_seen < current_date - %s AND EXISTS (SELECT 1 FROM raw.job_posting r"
                        " JOIN core.board b ON b.ats || ':' || b.token = r.payload->>'board' WHERE (r.source, r.job_url) ="
                        " (j.source, j.job_url) AND b.last_ok_at::date > j.last_seen)" + kept)
        aged_where = "j.first_seen < current_date - %s AND j.source <> 'manual'" + kept  # a job you added stays till you remove it
        for where, days in ((closed_where, CLOSED_AFTER_DAYS), (aged_where, KEEP_DAYS)):
            # your jobs' keys only: a website job deleted may come back (reposted, or remote), for you or the site
            cur.execute(f"INSERT INTO core.job_seen (job_key) SELECT job_key FROM core.job j WHERE j.in_scope AND {where}"
                        " ON CONFLICT (job_key) DO UPDATE SET seen_on = current_date", (days,))
        closed = archive_and_delete(cur, closed_where, (CLOSED_AFTER_DAYS,), "closed")
        aged = archive_and_delete(cur, aged_where, (KEEP_DAYS,), "aged")
        cur.execute("DELETE FROM raw.job_posting WHERE last_seen < current_date - %s", (WORK_DAYS,))
    conn.commit()
    yours = sum(job["in_scope"] for job in jobs.values())
    print(f"transform: {len(postings)} postings -> {yours} jobs yours, {len(jobs) - yours} website only, {len(deleted)} deleted"
          f" before; removed: {dropped} out"
          f" of scope, {closed} closed, {aged} over {KEEP_DAYS} days; not yours: "
          + ", ".join(f"{skipped[reason]} {reason}" for reason in ("no role", "too senior", "not in reach", "barred")))


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


SNIPPET = 300  # a description shorter than this is a board's snippet: the job's page is read for the whole text


def describe(conn) -> None:
    """Read the page of each new job that came without a description, for its schema.org
    JobPosting (Greenhouse, Lever, Workday, Workable ...); never a NO_FETCH page. Sites side by
    side, each site's pages 1 second apart; a site that answers 429 is left alone for as long as it
    asks (sources.fetch), and every page left when the step's 60 seconds are up waits for the next run. A page that
    says the job is closed (closed_reason), or is gone (404, 410), hides the job like a restriction does.
    Your jobs first, then the website's (a job with no text lists no skills: 31% of the published on 2026-10-17); a
    board's short snippet (NaukriGulf's, under SNIPPET characters) is read in full too."""
    todo = conn.execute(
        "SELECT job_id, job_url FROM core.job WHERE (description IS NULL OR length(description) < %s) AND described_at IS NULL"
        " AND job_url LIKE 'http%%' AND job_url !~ %s AND first_seen >= current_date - %s"
        " ORDER BY in_scope DESC, role_rank, job_id LIMIT 300", (SNIPPET, NO_FETCH, DAYS)).fetchall()
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
        cur.executemany("UPDATE core.job SET description = coalesce(%s, description), described_at = now() WHERE job_id = %s", read)
        cur.executemany("UPDATE core.job SET restriction = 'no longer open: ' || %s WHERE job_id = %s AND restriction IS NULL", closed)
    conn.commit()
    found = sum(text is not None for text, _ in read)
    if len(todo) >= 10 and found * 10 < len(todo):  # a site that hides its text wastes the step every run
        print(f"WARNING describe: only {found} of {len(todo)} pages gave a description - a site may hide it (config.NO_FETCH)")
    print(f"describe: {found} of {len(todo)} descriptions found"
          + reasons(page_gave_no_text=len(read) - found - sum(why == "page gone" for why, _ in closed), not_reached=len(todo) - len(read))
          + (f", {len(closed)} jobs closed (hidden)" if closed else ""))


# "4+ years of experience", "3-5 yrs of hands-on PySpark experience", "experience: 2 years", "minimum 3 years' experience"
YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*\d{1,2}\s*)?\+?\s*(?:years?|yrs?)['’]?\s*(?:of\s+)?(?:[\w/&.,+#-]+\s+){0,5}?experience"
                   r"|experience\s*(?:of|:)?\s*(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*\d{1,2}\s*)?(?:years?|yrs?)", re.I)


def years_required(text: str | None) -> int | None:
    """The most years of experience a posting asks for, by its lowest bound: "4+ years of experience ... 3+ years of
    PySpark experience" is 4. None when it names none in digits (grade_claude then asks Claude)."""
    found = [int(a or b) for a, b in YEARS.findall(text or "")]
    found = [n for n in found if 0 < n <= 20]
    return max(found) if found else None


# What a posting says about applying from abroad, matched against its title and description, any case. From Radarak's
# int_job_signals (MIT, Copyright (c) 2026 Moamen Moussa; see LICENSE-THIRD-PARTY).
GULF = r"(saudi|emirati|uae|qatari|kuwaiti|omani|bahraini)"
NATIONALS_TITLE = (r"\m(emirati|uae\s+nationals?|saudi\s+nationals?|qatari\s+nationals?|kuwaiti\s+nationals?|omani\s+nationals?"
                   r"|bahraini\s+nationals?)\M|\m(saudis?|emiratis?|qataris?|kuwaitis?|omanis?|bahrainis?)\s+only\M")
NATIONALS_TEXT = (GULF + r"\s+nationals?\s+(only|are\s+required|is\s+required|required)|only\s+(open\s+to\s+)?" + GULF
                  + r"\s+(nationals?|citizens)|nationals?\s+only|(saudi[sz]ation|emirati[sz]ation|qatari[sz]ation|omani[sz]ation)"
                  r"|must\s+be\s+(a\s+)?" + GULF + r"\s+(national|citizen)")
ARABIC_TITLE = r"\marabic\M"
ARABIC_TEXT = (r"(fluent|fluency|proficien\w*|native|bilingual|speak\w*|written\s+and\s+(spoken|verbal)|verbal\s+and\s+written"
               r"|excellent|strong|good\s+command\s+of)\W+(\w+\W+){0,4}arabic|arabic\W+(\w+\W+){0,3}(speaker|speaking|language"
               r"|required|is\s+a\s+must|essential|is\s+mandatory|fluency|proficiency|skills)|arabic\s+(and|&|/)\s+english"
               r"|english\s+(and|&|/)\s+arabic")
VISA_OFFERED = (r"visa\s+sponsor|sponsor(ship)?\s+(of\s+)?(your\s+|a\s+)?(work\s+)?(visa|permit)|relocation\s+(support|package"
                r"|assistance|bonus|allowance|budget|help|is\s+(provided|offered|available|supported))|(help|support|assist)\s+"
                r"(you\s+)?(with\s+)?(your\s+)?relocat|visa\s+(support|assistance|and\s+relocation)|work\s+permit\s+(support"
                r"|assistance)|blue\s+card|we\s+(offer|provide)\s+relocation")
VISA_REFUSED = (r"(no|not|unable\s+to|cannot|can't|do\s+not|don't|won't|will\s+not|without)\s+(\w+\s+){0,3}(visa\s+)?sponsor"
                r"|sponsorship\s+(is\s+)?not\s+(available|provided|offered|possible)|no\s+relocation"
                r"|relocation\s+(is\s+)?not\s+(available|provided|offered)")
WORLDWIDE = r"\m(anywhere|worldwide|global|international)\M|^\s*(fully\s+|100%\s+)?remote\s*(\(remote\))?\s*$|^\s*\(remote\)\s*$"


def match_skills(conn) -> None:
    """Store which of your skills each job of the window asks for (core.job_skill), so the email
    and the tracker read a small table instead of matching every job's text each time; then, for the same jobs, how the
    job is done (work_mode), the countries and regions it names (core.geo_pattern), what it says about visas, nationals
    and Arabic, and its pay as a year in dollars and euros (fx.py). Re-running it gives the same rows."""
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
        " AND years_required IS NULL", (WORK_DAYS,))]
    with conn.cursor() as cur:
        cur.executemany("UPDATE core.job SET years_required = %s WHERE job_id = %s", [a for a in asked if a[0]])
    # Outside home only remote jobs of yours are kept (config.in_reach), so one there is Remote; else its words decide. Its
    # location is matched with its place ("UAE", "Europe") unless that is Remote; a remote job open anywhere is Worldwide.
    conn.execute(r"""
        UPDATE core.job j SET work_mode = m.work_mode, countries = m.countries, regions = m.regions,
            role_family = core.role_family(j.title), seniority = core.seniority(j.title),
            track = (SELECT t.track FROM core.role_track t WHERE t.role_family = core.role_family(j.title)),
            nationals_only = m.text ~* %(nationals_text)s OR j.title ~* %(nationals_title)s,
            arabic_required = m.text ~* %(arabic_text)s OR j.title ~* %(arabic_title)s,
            visa_relocation = m.text ~* %(visa)s AND m.text !~* %(refused)s OR j.source = 'relocateme'  -- its every job
        FROM (SELECT x.job_id, w.work_mode, x.title || ' ' || coalesce(x.description, '') AS text,
                     CASE WHEN w.work_mode = 'Remote' AND x.location ~* %(worldwide)s THEN '{}'::text[]
                          ELSE coalesce(array_agg(DISTINCT g.country) FILTER (WHERE g.country IS NOT NULL), '{}') END AS countries,
                     CASE WHEN w.work_mode = 'Remote' AND x.location ~* %(worldwide)s THEN '{Worldwide}'::text[]
                          ELSE coalesce(array_agg(DISTINCT g.region) FILTER (WHERE g.region IS NOT NULL), '{}') END AS regions
              FROM core.job x
              CROSS JOIN LATERAL (SELECT CASE WHEN x.in_scope AND x.place <> %(home)s OR x.location || ' ' || x.title ~* '\mremote|work from home|\mwfh\M'
                                                   AND x.location || ' ' || x.title !~* %(not_remote)s THEN 'Remote'
                                              WHEN x.location || ' ' || x.title ~* '\mhybrid' THEN 'Hybrid' ELSE 'Onsite' END AS work_mode) w
              LEFT JOIN core.geo_pattern g
                ON x.location || CASE WHEN x.place IN ('Remote', 'Unknown location') THEN '' ELSE ' ' || x.place END ~* g.pattern
              WHERE x.first_seen >= current_date - %(days)s
              GROUP BY x.job_id, w.work_mode) m
        WHERE j.job_id = m.job_id""",
        {"nationals_text": NATIONALS_TEXT, "nationals_title": NATIONALS_TITLE, "arabic_text": ARABIC_TEXT,
         "arabic_title": ARABIC_TITLE, "visa": VISA_OFFERED, "refused": VISA_REFUSED, "worldwide": WORLDWIDE,
         "home": HOME, "not_remote": NOT_REMOTE, "days": DAYS})
    # a job whose place transform could not name: the one country its location names (Bengaluru: India; "Seoul, South
    # Korea (remote)" is South Korea, not Remote: a remote job tied to a country), else Remote when done remotely;
    # every such job, not only the window's (a job transform moves keeps its place)
    conn.execute("""
        UPDATE core.job SET place = CASE WHEN cardinality(countries) = 1 THEN countries[1] ELSE 'Remote' END
        WHERE place = 'Unknown location' AND (work_mode = 'Remote' OR cardinality(countries) = 1)""")
    # each day's new jobs and how many ask for each skill, every tech job counted (core.skill_day: the rising skills)
    conn.execute("""
        INSERT INTO core.skill_day AS d (day, skill, jobs, total_jobs)
        SELECT j.first_seen, k.skill, count(*), t.jobs FROM core.job j JOIN core.job_skill k USING (job_id)
        JOIN (SELECT first_seen, count(*) AS jobs FROM core.job GROUP BY first_seen) t USING (first_seen)
        WHERE j.first_seen >= current_date - %s GROUP BY j.first_seen, k.skill, t.jobs
        ON CONFLICT (day, skill) DO UPDATE SET jobs = EXCLUDED.jobs, total_jobs = EXCLUDED.total_jobs
        WHERE (d.jobs, d.total_jobs) IS DISTINCT FROM (EXCLUDED.jobs, EXCLUDED.total_jobs)""", (DAYS,))
    rates = fx.load_rates(conn)
    paid = fx.convert(conn, DAYS)
    conn.commit()
    print(f"match_skills: {matched} skill matches, years asked found in {sum(1 for a in asked if a[0])} of {len(asked)} descriptions;"
          f" {rates} ECB rates read, {paid} salaries converted to a year in USD and EUR")


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


# The jobs of one side (home or not) not emailed yet, first seen in the last WORK_DAYS (a day the email failed goes the
# next day), best first; Claude's score counts only with features.grade
EMAIL_JOBS = """
    SELECT s.*, j.description, r.payload->>'job_type' AS job_type FROM mart.job_status s
    JOIN core.job j USING (job_id) LEFT JOIN raw.job_posting r ON (r.source, r.job_url) = (s.source, s.job_url)
    WHERE s.emailed_at IS NULL AND s.first_seen >= current_date - %s AND (s.place = %s) = %s
      AND s.status NOT IN ('ignored', 'rejected')
    ORDER BY s.role_rank, s.target_company DESC, CASE WHEN %s THEN s.score END DESC NULLS LAST, s.skill_matches DESC,
             s.date_posted DESC NULLS LAST"""


def problems(conn) -> list[tuple[str, str, str]]:
    """This run's steps that did not end clean: (step, status, summary) for each step of the latest collect (since its
    schema step started) that failed, warned (it printed a WARNING: a source down, 0 rows where it had some, a board gone)
    or is still marked running longer than the longest timeout (runlog.STALE_SECONDS: killed); a step still running beside
    this one (tailor, outreach_drafts beside publish) is not. The run's own report is not one of them."""
    return conn.execute(
        "SELECT step, status, coalesce(summary, '') FROM core.step_run WHERE started_at >= coalesce("
        "(SELECT max(started_at) FROM core.step_run WHERE step = 'schema'), now() - interval '1 day')"
        " AND step NOT IN ('run_report', 'email_egypt', 'email_abroad')"
        " AND (status IN ('failed', 'warn') OR (status = 'running' AND started_at < now() - make_interval(secs => %s)))"
        " ORDER BY started_at", (runlog.STALE_SECONDS,)).fetchall()


SILENT = re.compile(r"0 postings today|answered \d{3}|has the site changed|has the board changed")  # a source silent or refusing
SILENT_SOURCES = 3  # this many in one run is an alert, not only a line in the email


DEAD_DAYS = 30  # a board read in this many days that gave no posting in them is dead (dead_boards)


def dead_boards(conn) -> list[str] | None:
    """The boards gone dead, one line each with how long: a board of core.extract_count (a source, a company board
    'ats:token', company_sites, an Apify board) read in the last DEAD_DAYS, counted since at least DEAD_DAYS ago, with 0
    postings in them; and, with features.market, an enabled Apify board (apify.boards) with no posting in them (a run that
    gives nothing is not counted: extract_apify loads only the boards that gave rows). None when the counts cover under
    DEAD_DAYS (a fresh warehouse, as on GitHub): nothing can be judged yet. Your career sites are only counted together
    (company_sites): core.company keeps no count per site."""
    today = conn.execute("SELECT current_date").fetchone()[0]
    counts = {board: (last, first, read) for board, last, first, read in conn.execute(
        "SELECT board, max(day) FILTER (WHERE postings > 0), min(day), max(day) FROM core.extract_count GROUP BY board")}
    since = today - timedelta(DEAD_DAYS)  # the window is the days after it
    if not counts or min(first for _, first, _ in counts.values()) > since:
        return None
    dead = {board: (last, first) for board, (last, first, read) in counts.items()
            if read > since and first <= since and (last is None or last <= since)}
    if FEATURES.get("market"):
        began = min(first for _, first, _ in counts.values())
        for name, board in ((SETTINGS.get("apify") or {}).get("boards") or {}).items():
            last, first, _ = counts.get(f"apify:{name}", (None, began, None))
            if board.get("enabled") and (last is None or last <= since):
                dead[f"apify:{name}"] = (last, first)
    return [f"{board}: 0 postings for {(today - (last or first)).days} days"
            + (f" (last on {last})" if last else " (none since counting began)") for board, (last, first) in sorted(dead.items())]


def run_report(conn) -> None:
    """The collect's last step (after publish): when a step failed, or was killed and left 'running', email Omar one alert
    that says so, and whether the website was still published ("published with 2 steps failing"); warnings alone go to
    the Egypt email's "Problems in this run", not here. Each failed task has already sent its own alert (alert.py).
    Once a Cairo month (its first run that can judge them) it prints a WARNING dead_boards: line per dead board (dead_boards)."""
    from .alert import send
    marker = OUTPUT / f"dead-boards-{cairo_today():%Y-%m}"  # the month the dead boards were listed: once a month
    if not marker.exists() and (dead := dead_boards(conn)) is not None:
        for line in dead:
            print(f"WARNING dead_boards: {line}")
        print(f"dead_boards: {len(dead)} boards read without a posting in {DEAD_DAYS} days, listed once this month")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    rows = problems(conn)
    failing = [(step, status, summary) for step, status, summary in rows if status != "warn"]
    # sources gone quiet or refusing us, inside extract steps that still ended (warn): many at once is a run that failed
    silent = [line for step, status, summary in rows if step.startswith("extract_") and status == "warn"
              for line in summary.split(" | ") if SILENT.search(line)]
    if len(silent) >= SILENT_SOURCES:
        send(f"job-radar: {len(silent)} sources silent or refusing in this run", silent)
        print(f"WARNING run_report: {len(silent)} sources silent or refusing")
    if not failing:
        print(f"run_report: no step failed ({len(rows)} with warnings, in today's Egypt email)")
        return
    published = conn.execute("SELECT status FROM core.step_run WHERE step = 'publish' AND started_at >= (SELECT max(started_at)"
                             " FROM core.step_run WHERE step = 'schema') ORDER BY started_at DESC LIMIT 1").fetchone()
    published = published and published[0] in ("ok", "warn")
    subject = (f"job-radar: published with {len(failing)} step{'s' * (len(failing) != 1)} failing" if published
               else f"job-radar FAILED: {len(failing)} step{'s' * (len(failing) != 1)}, the website was not published")
    send(subject, [f"{step} ({status}): {summary.splitlines()[-1] if summary else 'no output (killed?)'}"[:400]
                   for step, status, summary in failing])
    print(f"WARNING run_report: {subject}")


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
    jobs = conn.cursor(row_factory=dict_row).execute(EMAIL_JOBS, (WORK_DAYS, HOME, home, FEATURES["grade"])).fetchall()
    jobs = [j for j in jobs if not hidden_reason(j["status"], j["restriction"], j["score"])]
    if not FEATURES["grade"]:  # off: no score or reason in the email either
        for j in jobs:
            j["score"] = j["grade_reason"] = None
    follow_ups = [{"company": company, "title": title, "applied_at": day}
                  for company, title, day, _ in due_now(conn)[:10]] if home and FEATURES["follow_up"] else []
    sent_marker = OUTPUT / f"followups-emailed-{cairo_today()}"  # the Cairo day the follow-ups went out: a second collect that day has none to add
    issues = [f"{step} ({status}): {summary.splitlines()[-1] if summary else 'no output (killed?)'}"[:300]
              for step, status, summary in problems(conn)] if home else []
    if not jobs and not issues and (not follow_ups or sent_marker.exists()):
        print(f"email {name}: no new jobs, nothing sent")
        return
    title = subject(jobs, name, follow_ups) + (f" · {len(issues)} problem{'s' * (len(issues) != 1)}" if issues else "")
    if send(digest(jobs, name, follow_ups, issues), title, name):
        conn.execute("UPDATE core.job SET emailed_at = now() WHERE job_id = ANY(%s)", ([j["job_id"] for j in jobs],))
        conn.commit()
        if follow_ups:
            sent_marker.parent.mkdir(parents=True, exist_ok=True)
            sent_marker.touch()
