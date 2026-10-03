"""The pipeline steps, one Airflow task each (python -m job_radar <step>):

    schema -> extract_boards, extract_remote, extract_egypt, extract_companies, extract_portals,
              extract_email (side by side) -> transform -> describe -> match_skills -> email

A source that fails is a warning and the run goes on with the others; only a crash of the step
itself (the warehouse down, the email not sent) or no network at all fails its task.
"""

from __future__ import annotations

import html
import json
import os
import re
import smtplib
import socket
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import sources
from .config import (DESCRIBE_SECONDS, EXTRACT_SECONDS, HOURS_OLD, NO_FETCH, PLACES, ROLES, in_reach, is_target,
                     place_of, role_of, too_senior)

ROOT = Path(__file__).resolve().parent.parent
DAYS = HOURS_OLD // 24
ROLE_LABEL = {rank: label for rank, label, _, _ in ROLES}


def schema(conn) -> None:
    conn.execute((ROOT / "sql" / "schema.sql").read_text(encoding="utf-8"))
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
    extract(conn, sources.himalayas, sources.weworkremotely)


def extract_egypt(conn) -> None:
    extract(conn, sources.wuzzuf, sources.tanqeeb)


def extract_workable(conn) -> None:
    extract(conn, sources.workable_jobs)


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
    several boards, or reposted, is one job). Re-running it changes nothing."""
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
        rank = role_of(p["title"] or "")
        # the place searched, else the location's, else the title's ("Data Engineer - Cairo"); a job
        # whose place none of them gives (only job alerts get this far) is "Unknown location"
        place = p["searched_for"] or place_of(p["location"] or "") or place_of(p["title"] or "") or "Unknown location"
        if (not (rank and place and p["job_url"]) or too_senior(p["title"])
                or not in_reach(place, p["location"] or "", p["title"], bool(p["is_remote"]))):
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
        if place == "Remote":  # found by a remote search too: it is a remote job
            job["place"] = "Remote"
    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO core.job AS j (job_key, title, company, location, place, source, job_url, role_rank, role,
                                       target_company, date_posted, description, first_seen)
            VALUES (%(key)s, %(title)s, %(company)s, %(location)s, %(place)s, %(source)s, %(job_url)s, %(rank)s,
                    %(role)s, %(target)s, %(date_posted)s, %(description)s, %(first_seen)s)
            ON CONFLICT (job_key) DO UPDATE SET
                description = coalesce(j.description, EXCLUDED.description),
                target_company = j.target_company OR EXCLUDED.target_company,
                place = CASE WHEN EXCLUDED.place = 'Remote' THEN 'Remote' ELSE j.place END""",
            list(jobs.values()))
    conn.commit()
    print(f"transform: {len(postings)} postings -> {len(jobs)} jobs in scope")


def describe(conn) -> None:
    """Read the page of each new job that came without a description, for its schema.org
    JobPosting (Greenhouse, Lever, Workday, Workable ...); never a NO_FETCH page. Sites side by
    side, each site's pages 1 second apart; a site that answers 429, and every page left when the
    step's 60 seconds are up, waits for the next run."""
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
                r = requests.get(url, headers=sources.BROWSER,
                                 timeout=max(5, min(30, sources.time_left(DESCRIBE_SECONDS) - 5)))
            except requests.RequestException:
                continue
            if r.status_code == 429:
                break
            if r.status_code == 200:  # bytes: the page's own charset decides, not a guess
                read.append((page_description(r.content), job_id))
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
    """The Egypt email (Airflow: 12pm and 7pm Cairo time)."""
    email(conn, egypt=True)


def email_abroad(conn) -> None:
    """The email of the jobs outside Egypt: remote ones and Unknown location (8am and 8pm)."""
    email(conn, egypt=False)


def email(conn, egypt: bool) -> None:
    """Email the window's jobs in Egypt (or outside it) not emailed yet, by place, role and
    employment type, best first in each: target companies, then how many of your skills they ask
    for; nothing when there is no new job. Each job is marked emailed, so it is never sent twice.
    Without Gmail settings, write output/digest-<date>.html instead and mark nothing."""
    name = "Egypt" if egypt else "Outside Egypt"
    jobs = conn.cursor(row_factory=dict_row).execute(
        "SELECT s.*, j.description, r.payload->>'job_type' AS job_type FROM mart.job_status s"
        " JOIN core.job j USING (job_id) LEFT JOIN raw.job_posting r ON (r.source, r.job_url) = (s.source, s.job_url)"
        " WHERE s.emailed_at IS NULL AND s.first_seen >= current_date - %s AND (s.place = 'Egypt') = %s"
        " ORDER BY s.role_rank, s.target_company DESC, s.skill_matches DESC, s.date_posted DESC NULLS LAST",
        (DAYS, egypt)).fetchall()
    if not jobs:
        print(f"email {name}: no new jobs, nothing sent")
        return
    if send(digest(jobs, name), subject(jobs, name)):
        conn.execute("UPDATE core.job SET emailed_at = now() WHERE job_id = ANY(%s)", ([j["job_id"] for j in jobs],))
        conn.commit()


def subject(jobs: list[dict], name: str) -> str:
    starred = sum(j["target_company"] for j in jobs)
    return (f"Job radar · {name} · {len(jobs)} new job{'s' * (len(jobs) != 1)}"
            f"{f' · ⭐ {starred} at your companies' if starred else ''} · {date.today():%d %b}")


# The email is plain HTML with inline styles only, the one form every mail client (Gmail first)
# shows as designed: a header with the numbers, then a section per place, in it one per role in
# your order, in that one per employment type, one card per job. Cards stop at EMAIL_BYTES, so the
# email stays under Gmail's ~100 KB clip; the rest are a link away in the tracker.
EMAIL_BYTES = 70_000
PLACE_ORDER = list(dict.fromkeys(["Egypt", "Remote", *(place for place, _, _ in PLACES)]))
EMPLOYMENT = ["Full-time", "Part-time", "Contract", "Freelance"]
# Experience, so the further you scroll the more a job asks for: the title's words first, else the
# years the description asks for (1 or less: entry and junior, 5 or more: senior), else mid level
ENTRY = r"\b(intern(ship)?|trainee|graduate|fresh|entry|junior|jr)\b|متدرب|حديث التخرج|مبتدئ"
SENIOR = r"\b(senior|sr|expert)\b|\biii\b|خبير"
YEARS = (r"\b(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*\d{1,2}\s*)?\+?\s*(?:years?|yrs?)\b(?=[^.]{0,40}experience)"
         r"|experience[^.]{0,40}?\b(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*\d{1,2}\s*)?(?:years?|yrs?)\b"
         r"|خبرة[^.]{0,30}?(\d{1,2})")
LEVELS = ["Entry & junior", "Mid level", "Senior"]
INDEED_TYPES = {"fulltime": "Full-time", "parttime": "Part-time", "contract": "Contract", "temporary": "Contract"}
# the title's words, then only plain statements in the description ("smart contracts" is no contract job)
TYPE_IN_TITLE = {"Freelance": r"freelanc|عمل حر", "Part-time": r"part[- ]?time|دوام جزئي",
                 "Contract": r"\bcontract(or)?\b|fixed[- ]term|\btemp(orary)?\b"}
TYPE_IN_TEXT = {"Freelance": r"\bfreelanc", "Part-time": r"\bpart[- ]time\b",
                "Contract": r"\bcontract (role|position|basis|assignment)|\d+[- ]months? contract|fixed[- ]term"}
# Colours from the dataviz skill's reference palette: text in ink tokens, a deep blue shell, a
# tinted tag per job type.
FONT = "font-family:system-ui,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
INK, INK2, LINE, PLANE = "#0b0b0b", "#52514e", "#e1e0d9", "#f4f4f1"
SHELL, LINK, TINT = "#0d366b", "#1c5cab", "#eef4fc"
TYPE_TAG = {"Part-time": ("#ece9fb", "#3b2e8a"), "Contract": ("#fdeee6", "#9a3b12"), "Freelance": ("#e3f6ee", "#0e6b49")}


def experience(j: dict) -> tuple[int, int | None]:
    """(level, years): level 0 entry or junior, 1 mid level, 2 senior; years, the least the
    description asks for, None when it does not say."""
    m = re.search(YEARS, j.get("description") or "", re.I)
    years = int(next(g for g in m.groups() if g)) if m else None
    years = years if years is not None and years <= 15 else None  # "founded 50 years ago" is no requirement
    if re.search(ENTRY, j["title"], re.I):
        return 0, years
    if re.search(SENIOR, j["title"], re.I):
        return 2, years
    return (1 if years is None else 0 if years <= 1 else 2 if years >= 5 else 1), years


def employment(j: dict) -> str:
    """Full-time, Part-time, Contract or Freelance: Indeed's job type (full-time first when it gives
    two), else the title, else the description; Full-time when none says."""
    indeed = {INDEED_TYPES.get(kind) for kind in (j.get("job_type") or "").split(", ")}
    for kind in EMPLOYMENT:
        if kind in indeed:
            return kind
    for patterns, text in ((TYPE_IN_TITLE, j["title"]), (TYPE_IN_TEXT, j.get("description") or "")):
        for kind, pattern in patterns.items():
            if re.search(pattern, text, re.I):
                return kind
    return "Full-time"


def tag(text: str, background: str, color: str) -> str:
    return (f'<span style="display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;font-weight:600;'
            f'background:{background};color:{color}">{text}</span>')


def job_card(j: dict) -> str:
    """The title (the link), company · location and a quiet meta line. A job at your companies gets
    a warm ground and a "⭐ Your company" tag."""
    star = j["target_company"]
    kind = j.get("employment", "Full-time")
    source = "your job alerts" if j["source"] == "email" else html.escape(j["source"][:1].upper() + j["source"][1:])
    meta = " · ".join(x for x in (
        tag("⭐ Your company", "#fdf0c4", "#7a5200") if star else "",
        tag(kind, *TYPE_TAG[kind]) if kind in TYPE_TAG else "",
        f"{j['years']}+ yrs" if j.get("years") is not None else "",
        f"via {source}",
        f"{j['date_posted']:%d %b}" if j["date_posted"] else "",
        f"CV {j['cv_coverage']:.0f}%" if j.get("cv_coverage") is not None else "") if x)
    where = " · ".join(html.escape(x) for x in (str(j["company"] or "").strip(" -"), str(j["location"] or "").strip(" -")) if x)
    return (f'<tr><td style="padding:14px 20px;border-top:1px solid {LINE};{"background:#fffaeb;" if star else ""}">'
            f'<a href="{html.escape(j["job_url"])}" style="font-size:16px;line-height:22px;font-weight:600;color:{LINK};'
            f'text-decoration:none">{html.escape(j["title"])}</a>'
            f'<div style="margin-top:2px;font-size:14px;line-height:20px;color:{INK}">{where}</div>'
            f'<div style="margin-top:4px;font-size:12px;line-height:20px;color:{INK2}">{meta}</div></td></tr>')


def digest(jobs: list[dict], name: str) -> str:
    """One column at most 640 px wide, so it reads the same on a phone and on a laptop: a deep blue
    header with one summary line and a chip per place, then a band per place (Egypt first), in it
    a section per experience level (entry and junior, mid, senior: the further you scroll, the more
    a job asks for), a heading per role, a job-type line only when a role mixes types, and the cards."""
    for j in jobs:
        j["employment"] = employment(j)
        j["level"], j["years"] = experience(j)
    # fewest years first inside each level (a job that does not say sits with its level's usual
    # years); stable, so the best match stays first among equals
    jobs = sorted(jobs, key=lambda j: (j["level"], (0, 2, 5)[j["level"]] if j["years"] is None else j["years"]))
    places = sorted({j["place"] for j in jobs},
                    key=lambda p: PLACE_ORDER.index(p) if p in PLACE_ORDER else len(PLACE_ORDER))
    by_place = {place: [j for j in jobs if j["place"] == place] for place in places}
    starred = sum(j["target_company"] for j in jobs)
    summary = f"{len(jobs)} new job{'s' * (len(jobs) != 1)}{f' · ⭐ {starred} at your companies' if starred else ''}"
    first = by_place[places[0]][0]
    # the inbox preview line under the subject
    preheader = f"{summary} · top: {first['title']} at {first['company']}"
    chips = "".join(f'<span style="display:inline-block;margin:0 6px 6px 0;padding:4px 10px;border-radius:12px;'
                    f'background:#184f95;font-size:12px;color:#cde2fb">{html.escape(place)} '
                    f'<b style="color:#ffffff">{len(group)}</b></span>' for place, group in by_place.items()
                    ) if len(by_place) > 1 else ""  # one place (the Egypt email): the title already names it
    more = (f'<a href="http://127.0.0.1:8501" style="color:{LINK};text-decoration:none">in your tracker →</a>')
    sections = []
    for place, in_place in by_place.items():
        if len(by_place) > 1:
            sections.append(
                f'<tr><td style="padding:16px 20px 14px;background:{TINT};border-top:1px solid {LINE}">'
                f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
                f'<td style="font-size:20px;line-height:26px;font-weight:700;color:{INK}">{html.escape(place)}</td>'
                f'<td align="right" style="font-size:13px;color:{INK2};white-space:nowrap">{len(in_place)} '
                f'job{"s" * (len(in_place) != 1)}</td></tr></table></td></tr>')
        for lvl, level_name in enumerate(LEVELS):
            in_level = [j for j in in_place if j["level"] == lvl]
            if not in_level:
                continue
            sections.append(
                f'<tr><td style="padding:22px 20px 6px;border-bottom:2px solid {SHELL}"><table role="presentation" '
                f'width="100%" cellpadding="0" cellspacing="0"><tr><td style="font-size:16px;line-height:22px;'
                f'font-weight:700;color:{SHELL}">{html.escape(level_name)}</td><td align="right" style="font-size:13px;'
                f'color:{INK2}">{len(in_level)}</td></tr></table></td></tr>')
            for rank, label in ROLE_LABEL.items():
                in_role = [j for j in in_level if j["role_rank"] == rank]
                if not in_role:
                    continue
                sections.append(
                    f'<tr><td style="padding:16px 20px 8px"><table role="presentation" width="100%" cellpadding="0" '
                    f'cellspacing="0"><tr><td style="font-size:12px;font-weight:700;letter-spacing:.6px;color:{LINK}">'
                    f'{html.escape(label.upper())}</td><td align="right" style="font-size:12px;color:{INK2}">{len(in_role)}'
                    f'</td></tr></table></td></tr>')
                if sum(map(len, sections)) >= EMAIL_BYTES:  # no room left: one line for the whole role
                    counts = " · ".join(f"{kind} {n}" for kind in EMPLOYMENT
                                        if (n := sum(j["employment"] == kind for j in in_role)))
                    sections.append(f'<tr><td style="padding:0 20px 14px;font-size:13px;color:{INK2}">{counts} · {more}'
                                    f'</td></tr>')
                    continue
                mixed = len({j["employment"] for j in in_role}) > 1
                for kind in EMPLOYMENT:
                    group = [j for j in in_role if j["employment"] == kind]
                    if not group:
                        continue
                    # cards stop at EMAIL_BYTES, the rest wait in the tracker
                    cards = []
                    for j in group:
                        if sum(map(len, sections)) + sum(map(len, cards)) >= EMAIL_BYTES:
                            break
                        cards.append(job_card(j))
                    shown = group[:len(cards)]
                    sections.append(
                        (f'<tr><td style="padding:8px 20px 8px">{tag(f"{kind} · {len(group)}", *TYPE_TAG.get(kind, (PLANE, INK2)))}'
                         f'</td></tr>' if mixed else "")
                        + "".join(cards)
                        + (f'<tr><td style="padding:10px 20px 14px;border-top:1px solid {LINE};font-size:13px;color:{INK2}">'
                           f'+ {len(group) - len(shown)} more {kind.lower()} {more}</td></tr>'
                           if len(group) > len(shown) else ""))
    return (f'<div style="margin:0;padding:16px 8px;background:{PLANE};{FONT}">'
            f'<div style="display:none;max-height:0;overflow:hidden;opacity:0">{html.escape(preheader)}</div>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;'
            f'margin:0 auto;background:#ffffff;border-radius:14px;overflow:hidden;border:1px solid {LINE};{FONT}">'
            f'<tr><td style="padding:24px 20px 18px;background:{SHELL}">'
            f'<div style="font-size:24px;line-height:30px;font-weight:700;color:#ffffff">Job radar · {html.escape(name)}</div>'
            f'<div style="margin-top:2px;font-size:13px;line-height:18px;color:#9ec5f4">{date.today():%A %d %B %Y}</div>'
            f'<div style="margin:14px 0 12px;font-size:16px;line-height:22px;font-weight:600;color:#ffffff">'
            f'{html.escape(summary)}</div>{chips}</td></tr>'
            + "".join(sections)
            + f'<tr><td style="padding:18px 20px 22px;border-top:1px solid {LINE};font-size:12px;line-height:18px;'
            f'color:{INK2}">Entry and junior jobs first, senior last; in each, your companies first, then the best matches. '
            f'Each job is sent once.<br>On your laptop: the <a href="http://127.0.0.1:8501" style="color:{LINK}">'
            f'tracker</a> to mark what you apply to · <a href="http://127.0.0.1:8081" style="color:{LINK}">Airflow'
            f'</a> collects at 1am, 7am, 11am and 6pm · Egypt email 12pm and 7pm, outside Egypt 8am and 8pm '
            f'(Cairo time).</td></tr></table></div>')


def send(body: str, title: str) -> bool:
    """Email the digest; without Gmail settings, write it to output/ instead and return False."""
    user, password = os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASSWORD")
    if not (user and password):
        (ROOT / "output").mkdir(exist_ok=True)
        path = ROOT / "output" / f"digest-{date.today()}.html"
        path.write_text(body, encoding="utf-8")
        print(f"email: no Gmail settings, wrote {path}")
        return False
    msg = EmailMessage()
    msg["Subject"] = title
    msg["From"] = user
    msg["To"] = os.environ.get("MAIL_TO") or user
    msg.set_content(f"{title}. Open this email as HTML to see them.")
    msg.add_alternative(body, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(user, password.replace(" ", ""))
        smtp.send_message(msg)
    print(f"email: sent '{title}' to {msg['To']}")
    return True
