"""The pipeline steps, one Airflow task each (python -m job_radar <step>):

    schema -> extract_boards, extract_remote, extract_egypt, extract_workable, extract_freehire,
              extract_companies, extract_portals, extract_email (side by side) -> transform -> describe -> match_skills
              -> export_career_ops
    email_egypt, email_abroad: the two emails, each at its own times (the HTML is in digest.py)

A source that fails is a warning and the run goes on with the others; only a crash of the step
itself (the warehouse down, the email not sent) or no network at all fails its task.
"""

from __future__ import annotations

import html
import json
import re
import socket
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import sources
from .config import (DESCRIBE_SECONDS, EXTRACT_SECONDS, HOME, HOURS_OLD, NO_FETCH, ROLE_LABEL, ROOT, SETTINGS,
                     in_reach, is_target, place_of, role_of, too_senior)
from .digest import digest, send, subject

DAYS = HOURS_OLD // 24


def schema(conn) -> None:
    """The warehouse's tables, then your companies: core.company is made the same as settings.yaml
    (companies), so a company removed there is removed here. A changed link is read and its
    platform detected again."""
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
    link) is skipped, so re-running a step adds nothing twice. NUL characters, which Postgres
    refuses, are dropped."""
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
    print(f"{name}: {len(rows)} postings, {new} new")


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
    extract(conn, sources.job_boards, sources.jooble)


def extract_remote(conn) -> None:
    extract(conn, sources.himalayas, sources.weworkremotely, sources.relomote)


def extract_egypt(conn) -> None:
    extract(conn, sources.wuzzuf, sources.tanqeeb)


def extract_workable(conn) -> None:
    extract(conn, sources.workable_jobs)


def extract_freehire(conn) -> None:
    extract(conn, sources.freehire)


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
    seen = {r[0] for r in conn.execute(
        "SELECT DISTINCT payload->>'message_id' FROM raw.job_posting WHERE source = 'email'")}

    def mailboxes():
        return sources.mailboxes(seen)

    extract(conn, mailboxes)


def job_key(title: str, company: str, job_url: str) -> str:
    """One key per job however the boards word it: the title without its bracketed notes and its
    trailing ' - City' / ' | Remote' / ' / ...' part, the company without its legal suffix, both
    lower case without punctuation. 'Data Engineer - Cairo (Hybrid)' at 'Acme Ltd.' and
    'Data Engineer' at 'ACME' are one job. A job with no company is keyed by its link."""
    def words(s: str) -> str:
        return " ".join(re.sub(r"[^\w]+", " ", s).split())
    title = re.split(r"\s+[-–—|/@]\s+", re.sub(r"\(.*?\)|\[.*?\]", " ", title.lower()))[0]
    company = re.sub(r"\b(llc|ltd|limited|inc|corp|corporation|co|company|plc|gmbh|s\.?a\.?e|fze|fzco)\b", " ",
                     company.lower())
    return f"{words(title)} | {words(company) or job_url}"


def transform(conn) -> None:
    """raw -> core.job: the window's postings with a role, not above senior, in a place in scope and
    in reach (remote, or onsite/hybrid in Cairo or Giza), one row per job (job_key: the same job on
    several boards, or reposted, is one job). A job of the window not emailed yet that no longer
    passes the rules (they changed) is removed. Re-running it changes nothing."""
    postings = conn.cursor(row_factory=dict_row).execute(
        "SELECT run_date, source, searched_for, title, company, location, job_url, date_posted, description,"
        " payload->>'is_remote' = 'true' AS is_remote FROM raw.job_posting WHERE run_date >= current_date - %s"
        " ORDER BY posting_id", (DAYS,)).fetchall()
    # your companies: a name of 4+ letters as a whole word ("Vodafone Egypt" is Vodafone); a shorter
    # one only as the whole company name, or "ag" and "db" would star "Siemens AG" and "DB Schenker"
    def letters(name: str) -> str:
        return re.sub(r"[^a-z0-9]", "", (name or "").lower())
    names = [name for (name,) in conn.execute("SELECT company FROM core.company WHERE starred")]
    short = {letters(n) for n in names if len(letters(n)) <= 3}
    yours = re.compile("|".join(rf"\b{re.escape(n)}\b" for n in names if len(letters(n)) > 3) or r"(?!)", re.I)
    jobs: dict[str, dict] = {}
    for p in postings:
        rank = role_of(p["title"] or "",
                       alert=p["source"] == "email" and "linkedin.com/jobs/view/" in (p["job_url"] or ""))
        # the place searched, else the location's, else the title's ("Data Engineer - Cairo"); a job
        # whose place none of them gives (only job alerts get this far) is "Unknown location"
        place = p["searched_for"] or place_of(p["location"] or "") or place_of(p["title"] or "") or "Unknown location"
        if (not (rank and place and p["job_url"]) or too_senior(p["title"])
                or not in_reach(place, p["location"] or "", p["title"], bool(p["is_remote"]), p["description"] or "")):
            continue
        key = job_key(p["title"], p["company"], p["job_url"])
        job = jobs.setdefault(key, {
            "key": key, "title": p["title"].strip(), "company": p["company"], "location": p["location"],
            "place": place, "source": p["source"], "job_url": p["job_url"], "rank": rank,
            "role": ROLE_LABEL[rank], "date_posted": p["date_posted"],
            "target": (is_target(p["company"]) or yours.search(p["company"]) is not None
                       or letters(p["company"]) in short),
            "description": p["description"], "first_seen": p["run_date"]})
        job["description"] = job["description"] or p["description"]
        if place == "Remote" and job["place"] != "Remote":  # found by a remote search too: a remote
            # job, shown with that remote listing's location and link
            job.update(place="Remote", location=p["location"], source=p["source"], job_url=p["job_url"])
    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO core.job AS j (job_key, title, company, location, place, source, job_url, role_rank, role,
                                       target_company, date_posted, description, first_seen)
            VALUES (%(key)s, %(title)s, %(company)s, %(location)s, %(place)s, %(source)s, %(job_url)s, %(rank)s,
                    %(role)s, %(target)s, %(date_posted)s, %(description)s, %(first_seen)s)
            ON CONFLICT (job_key) DO UPDATE SET
                description = coalesce(j.description, EXCLUDED.description),
                target_company = j.target_company OR EXCLUDED.target_company,
                place = CASE WHEN EXCLUDED.place = 'Remote' THEN 'Remote' ELSE j.place END,
                location = CASE WHEN EXCLUDED.place = 'Remote' THEN EXCLUDED.location ELSE j.location END,
                source = CASE WHEN EXCLUDED.place = 'Remote' THEN EXCLUDED.source ELSE j.source END,
                job_url = CASE WHEN EXCLUDED.place = 'Remote' THEN EXCLUDED.job_url ELSE j.job_url END""",
            list(jobs.values()))
        cur.execute("DELETE FROM core.job j WHERE first_seen >= current_date - %s AND emailed_at IS NULL"
                    " AND job_key <> ALL(%s) AND NOT EXISTS (SELECT 1 FROM core.application a WHERE a.job_id = j.job_id)",
                    (DAYS, list(jobs)))
        dropped = cur.rowcount
    conn.commit()
    print(f"transform: {len(postings)} postings -> {len(jobs)} jobs in scope, {dropped} no longer in scope removed")


def describe(conn) -> None:
    """Read the page of each new job that came without a description, for its schema.org
    JobPosting (Greenhouse, Lever, Workday, Workable ...); never a NO_FETCH page. Sites side by
    side, each site's pages 1 second apart; a site that answers 429 is left alone for as long as it
    asks (sources.fetch), and every page left when the step's 60 seconds are up waits for the next run."""
    todo = conn.execute(
        "SELECT job_id, job_url FROM core.job WHERE description IS NULL AND described_at IS NULL"
        " AND job_url LIKE 'http%%' AND job_url !~ %s AND first_seen >= current_date - %s"
        " ORDER BY role_rank, job_id LIMIT 300", (NO_FETCH, DAYS)).fetchall()
    by_site: dict[str, list] = defaultdict(list)
    for job_id, url in todo:
        by_site[urlsplit(url).netloc].append((job_id, url))

    def read_site(jobs: list) -> list[tuple]:
        read = []
        for job_id, url in jobs:
            if sources.time_left(DESCRIBE_SECONDS) < 10:  # room left for one page and the save
                break
            time.sleep(1)
            try:
                r = sources.fetch("GET", url, timeout=max(5, min(30, sources.time_left(DESCRIBE_SECONDS) - 5)))
            except sources.Held:  # the site asked us to wait: none of its pages this run
                break
            except sources.ERRORS:  # not read: tried again next run
                continue
            read.append((page_description(r.content), job_id))  # bytes: the page's own charset decides
        return read

    with ThreadPoolExecutor(8) as pool:
        read = [x for site in pool.map(read_site, by_site.values()) for x in site]
    with conn.cursor() as cur:
        cur.executemany("UPDATE core.job SET description = %s, described_at = now() WHERE job_id = %s", read)
    conn.commit()
    print(f"describe: {sum(text is not None for text, _ in read)} of {len(todo)} descriptions found")


def match_skills(conn) -> None:
    """Store which of your skills each job of the window asks for (core.job_skill), so the email
    and the tracker read a small table instead of matching every job's text each time.
    Re-running it gives the same rows."""
    window = "SELECT job_id FROM core.job WHERE first_seen >= current_date - %s"
    conn.execute(f"DELETE FROM core.job_skill WHERE job_id IN ({window})", (DAYS,))
    matched = conn.execute(
        "INSERT INTO core.job_skill (job_id, skill) SELECT j.job_id, s.skill FROM core.job j"
        " JOIN core.skill s ON j.title || ' ' || coalesce(j.description, '') ~* s.pattern"
        f" WHERE j.job_id IN ({window})", (DAYS,)).rowcount
    conn.execute("DELETE FROM core.cv_skill")  # your CVs: few and small, matched again every run
    conn.execute("INSERT INTO core.cv_skill (label, skill) SELECT c.label, s.skill FROM core.cv c"
                 " JOIN core.skill s ON c.text ~* s.pattern")
    conn.commit()
    print(f"match_skills: {matched} skill matches")


def export_career_ops(conn) -> None:
    """Hand the window's best matches to career-ops (github.com/career-ops-hq/career-ops), which
    scores them against your CV and tailors it when you run it in Claude Code: writes
    output/career-ops/pipeline.md (one job link per line, best first) and cv.md (your newest CV).
    Copy both into your career-ops folder (data/pipeline.md, cv.md)."""
    jobs = conn.execute(
        "SELECT job_url, title, company, place, skill_matches FROM mart.job_status"
        " WHERE first_seen >= current_date - %s AND skill_matches >= 3 AND status NOT IN ('ignored', 'rejected')"
        " ORDER BY role_rank, target_company DESC, skill_matches DESC LIMIT 25", (DAYS,)).fetchall()
    folder = ROOT / "output" / "career-ops"
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
    """The email of your home's jobs (settings.yaml: home, schedule.home_email)."""
    email(conn, home=True)


def email_abroad(conn) -> None:
    """The email of the jobs everywhere else: remote only, no hybrid or onsite (schedule.abroad_email)."""
    email(conn, home=False)


def email(conn, home: bool) -> None:
    """Email the window's jobs at home (or outside it) not emailed yet, by place, experience, role
    and employment type, best first in each: target companies, then how many of your skills they
    ask for; nothing when there is no new job. Each job is marked emailed, so it is never sent
    twice. Without Gmail settings, write it to output/ instead and mark nothing."""
    name = HOME if home else f"Outside {HOME}"
    jobs = conn.cursor(row_factory=dict_row).execute(
        "SELECT s.*, j.description, r.payload->>'job_type' AS job_type FROM mart.job_status s"
        " JOIN core.job j USING (job_id) LEFT JOIN raw.job_posting r ON (r.source, r.job_url) = (s.source, s.job_url)"
        " WHERE s.emailed_at IS NULL AND s.first_seen >= current_date - %s AND (s.place = %s) = %s"
        " ORDER BY s.role_rank, s.target_company DESC, s.skill_matches DESC, s.date_posted DESC NULLS LAST",
        (DAYS, HOME, home)).fetchall()
    if not jobs:
        print(f"email {name}: no new jobs, nothing sent")
        return
    if send(digest(jobs, name), subject(jobs, name), name):
        conn.execute("UPDATE core.job SET emailed_at = now() WHERE job_id = ANY(%s)", ([j["job_id"] for j in jobs],))
        conn.commit()
