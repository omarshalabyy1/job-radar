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
from .config import (DESCRIBE_SECONDS, EXTRACT_SECONDS, HOURS_OLD, NO_FETCH, ROLES, is_target, place_of, role_of,
                     too_senior)

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
    """raw -> core.job: the window's postings with a role, not above senior, and in a place in scope,
    one row per job (job_key: the same job on several boards, or reposted, is one job). Re-running
    it changes nothing."""
    postings = conn.cursor(row_factory=dict_row).execute(
        "SELECT run_date, source, searched_for, title, company, location, job_url, date_posted, description"
        " FROM raw.job_posting WHERE run_date >= current_date - %s"
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
        place = p["searched_for"] or place_of(p["location"] or "")
        if not (rank and place and p["job_url"]) or too_senior(p["title"]):
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


def email(conn) -> None:
    """Email the window's jobs not emailed yet, best first: role rank, target companies, then how
    many of your skills they ask for; nothing when there is no new job. Each job is marked
    emailed, so it is never sent twice. Without Gmail settings, write output/digest-<date>.html
    instead and mark nothing."""
    jobs = conn.cursor(row_factory=dict_row).execute(
        "SELECT * FROM mart.job_status WHERE emailed_at IS NULL AND first_seen >= current_date - %s"
        " ORDER BY role_rank, target_company DESC, skill_matches DESC, date_posted DESC NULLS LAST",
        (DAYS,)).fetchall()
    if not jobs:
        print("email: no new jobs, nothing sent")
        return
    if send(digest(jobs), subject(jobs)):
        conn.execute("UPDATE core.job SET emailed_at = now() WHERE job_id = ANY(%s)", ([j["job_id"] for j in jobs],))
        conn.commit()


def subject(jobs: list[dict]) -> str:
    starred = sum(j["target_company"] for j in jobs)
    return (f"Job radar · {len(jobs)} new job{'s' * (len(jobs) != 1)}"
            f"{f' · ⭐ {starred} at your companies' if starred else ''} · {date.today():%d %b}")


# The email is plain HTML with inline styles only, the one form every mail client (Gmail first)
# shows as designed: a header with today's numbers, then one section per role, one card per job.
# Each role shows its best EMAIL_PER_ROLE jobs, so the email stays under Gmail's ~100 KB clip;
# the rest are a link away in the tracker.
EMAIL_PER_ROLE = 6
FONT = "font-family:Segoe UI,Helvetica,Arial,sans-serif"
INK, MUTED, LINE, ACCENT = "#0f172a", "#64748b", "#e2e8f0", "#2563eb"


def badge(text: str, color: str, background: str) -> str:
    return (f'<span style="display:inline-block;padding:2px 8px;margin:2px 4px 2px 0;border-radius:10px;'
            f'font-size:12px;color:{color};background:{background};{FONT}">{html.escape(text)}</span>')


def job_card(j: dict) -> str:
    skills = [s for s in (j["skills_matched"] or "").split(", ") if s]
    star = "⭐ " if j["target_company"] else ""
    posted = badge(f"posted {j['date_posted']:%d %b}", "#334155", "#f1f5f9") if j["date_posted"] else ""
    if j.get("cv_coverage") is not None:
        posted += badge(f"your CV covers {j['cv_coverage']:.0f}%", "#5b21b6", "#ede9fe")
    meta =" · ".join(html.escape(str(x)) for x in (j["company"], j["location"] or j["place"]) if x)
    skill_line = ("".join(badge(s, "#065f46", "#d1fae5") for s in skills[:10])
                  + (badge(f"+{len(skills) - 10} more", "#065f46", "#ecfdf5") if len(skills) > 10 else "")
                  if skills else f'<span style="font-size:12px;color:{MUTED};{FONT}">'
                  f'{"none of your skills named" if j["described"] else "no description yet: open the job"}</span>')
    count = (f'<span style="float:right;padding:2px 10px;border-radius:10px;font-size:12px;font-weight:600;'
             f'color:#ffffff;background:{"#059669" if len(skills) >= 5 else "#10b981" if skills else "#94a3b8"};{FONT}">'
             f'{len(skills)} skill{"s" * (len(skills) != 1)}</span>')
    return (f'<tr><td style="padding:14px 16px;border-bottom:1px solid {LINE};'
            f'{"background:#fffbeb;" if star else ""}">{count}'
            f'<a href="{html.escape(j["job_url"])}" style="font-size:16px;font-weight:600;color:{ACCENT};'
            f'text-decoration:none;{FONT}">{star}{html.escape(j["title"])}</a>'
            f'<div style="margin:4px 0 6px;font-size:13px;color:{INK};{FONT}">{meta}</div>'
            f'<div>{badge(j["place"], "#1e3a8a", "#dbeafe")}{badge(j["source"], "#334155", "#f1f5f9")}'
            f'{posted}</div>'
            f'<div style="margin-top:6px">{skill_line}</div></td></tr>')


def digest(jobs: list[dict]) -> str:
    by_place: dict[str, int] = defaultdict(int)
    for j in jobs:
        by_place[j["place"]] += 1
    starred = sum(j["target_company"] for j in jobs)
    numbers = "".join(
        f'<td align="center" style="padding:10px 6px"><div style="font-size:24px;font-weight:700;color:#ffffff;{FONT}">'
        f'{value}</div><div style="font-size:12px;color:#cbd5e1;{FONT}">{html.escape(label)}</div></td>'
        for value, label in [(len(jobs), "new jobs"), (starred, "⭐ your companies"),
                             *sorted(((n, p) for p, n in by_place.items()), reverse=True)[:3]])
    sections = []
    for rank, label in ROLE_LABEL.items():
        group = [j for j in jobs if j["role_rank"] == rank]
        if group:
            sections.append(
                f'<tr><td style="padding:22px 16px 8px;border-bottom:2px solid {ACCENT}">'
                f'<span style="font-size:13px;font-weight:700;letter-spacing:.5px;color:{ACCENT};{FONT}">'
                f'{rank} · {html.escape(label.upper())}</span>'
                f'<span style="float:right;font-size:13px;color:{MUTED};{FONT}">{len(group)}</span></td></tr>'
                + "".join(job_card(j) for j in group[:EMAIL_PER_ROLE])
                + (f'<tr><td style="padding:10px 16px;font-size:13px;{FONT}"><a href="http://127.0.0.1:8501" '
                   f'style="color:{ACCENT};text-decoration:none">+ {len(group) - EMAIL_PER_ROLE} more in your tracker'
                   f'</a></td></tr>' if len(group) > EMAIL_PER_ROLE else ""))
    return (f'<div style="margin:0;padding:24px 0;background:#f1f5f9">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:680px;'
            f'margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid {LINE}">'
            f'<tr><td style="padding:24px 16px 8px;background:{INK}">'
            f'<div style="font-size:22px;font-weight:700;color:#ffffff;{FONT}">Job radar</div>'
            f'<div style="font-size:13px;color:#cbd5e1;{FONT}">{date.today():%A %d %B %Y} · best match first: '
            f'your role order, ⭐ your companies, then how many of your skills each job asks for</div></td></tr>'
            f'<tr><td style="background:{INK};padding:0 10px 16px"><table role="presentation" width="100%">'
            f'<tr>{numbers}</tr></table></td></tr>'
            + "".join(sections)
            + f'<tr><td style="padding:18px 16px;font-size:12px;color:{MUTED};{FONT}">'
            f'Mark what you apply to in the <a href="http://127.0.0.1:8501" style="color:{ACCENT}">tracker</a> · '
            f'runs in <a href="http://127.0.0.1:8081" style="color:{ACCENT}">Airflow</a> every 6 hours · '
            f'each job is sent once</td></tr></table></div>')


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
