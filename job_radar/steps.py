"""The pipeline steps, one Airflow task each (python -m job_radar <step>):

    schema -> extract_boards, extract_portals, extract_email -> transform -> describe -> score -> email

A source that fails is a warning and the run goes on with the others; only a crash of the step
itself (the warehouse down, the email not sent) fails its task.
"""

from __future__ import annotations

import html
import json
import os
import smtplib
import time
from datetime import date
from email.message import EmailMessage
from pathlib import Path

import anthropic
import requests
from bs4 import BeautifulSoup
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import ai, sources
from .config import HOURS_OLD, ROLES, is_target, place_of, role_of, too_senior

ROOT = Path(__file__).resolve().parent.parent
DAYS = HOURS_OLD // 24
ROLE_LABEL = {rank: label for rank, label, _, _ in ROLES}
BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/129.0 Safari/537.36"}


def schema(conn) -> None:
    conn.execute((ROOT / "sql" / "schema.sql").read_text(encoding="utf-8"))
    conn.commit()


def load(conn, name: str, rows: list[dict]) -> None:
    """Append one source's rows to raw.job_posting (without NUL characters, which Postgres refuses)."""
    clean = [{**{k: v.replace("\x00", "") if isinstance(v, str) else v for k, v in r.items()},
              "payload": Jsonb(json.loads(json.dumps(r["payload"], default=str).replace("\\u0000", "")))}
             for r in rows]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO raw.job_posting (run_date, source, searched_for, title, company, location, job_url,"
            " date_posted, description, payload) VALUES (current_date, %(source)s, %(searched_for)s, %(title)s,"
            " %(company)s, %(location)s, %(job_url)s, %(date_posted)s, %(description)s, %(payload)s)", clean)
    conn.commit()
    print(f"{name}: {len(rows)} postings")


def extract(conn, *source_functions) -> None:
    for source in source_functions:
        try:
            rows = source()
        except Exception as e:  # one source down is a short day, not a failed run
            print(f"WARNING {source.__name__}: {e!r}"[:300])
            continue
        load(conn, source.__name__, rows)


def extract_boards(conn) -> None:
    extract(conn, sources.job_boards, sources.himalayas, sources.weworkremotely, sources.workable, sources.jooble)


def extract_portals(conn) -> None:
    extract(conn, sources.company_portals)


def extract_email(conn) -> None:
    seen = {r[0] for r in conn.execute(
        "SELECT DISTINCT payload->>'message_id' FROM raw.job_posting WHERE source = 'email'")}

    def mailboxes():
        return sources.mailboxes(seen)

    extract(conn, mailboxes)


def transform(conn) -> None:
    """raw -> core.job: the window's postings with a role, not too senior and in a place in scope,
    one row per job (same title and company on any board). Re-running it changes nothing."""
    postings = conn.cursor(row_factory=dict_row).execute(
        "SELECT run_date, source, searched_for, title, company, location, job_url, date_posted, description,"
        " payload->>'skill_level' AS level FROM raw.job_posting WHERE run_date >= current_date - %s"
        " ORDER BY posting_id", (DAYS,)).fetchall()
    jobs: dict[str, dict] = {}
    for p in postings:
        rank = role_of(p["title"] or "")
        place = p["searched_for"] or place_of(p["location"] or "")
        if not (rank and place and p["job_url"]) or too_senior(p["title"]) or p["level"] == "senior":
            continue
        key = " | ".join(" ".join(s.lower().split()) for s in (p["title"], p["company"]))
        job = jobs.setdefault(key, {
            "key": key, "title": p["title"].strip(), "company": p["company"], "location": p["location"],
            "place": place, "source": p["source"], "job_url": p["job_url"], "rank": rank,
            "role": ROLE_LABEL[rank], "target": is_target(p["company"]), "date_posted": p["date_posted"],
            "description": p["description"], "first_seen": p["run_date"], "last_seen": p["run_date"]})
        job["last_seen"] = max(job["last_seen"], p["run_date"])
        job["description"] = job["description"] or p["description"]
        if place == "Remote":  # found by a remote search too: it is a remote job
            job["place"] = "Remote"
    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO core.job AS j (job_key, title, company, location, place, source, job_url, role_rank, role,
                                       target_company, date_posted, description, first_seen, last_seen)
            VALUES (%(key)s, %(title)s, %(company)s, %(location)s, %(place)s, %(source)s, %(job_url)s, %(rank)s,
                    %(role)s, %(target)s, %(date_posted)s, %(description)s, %(first_seen)s, %(last_seen)s)
            ON CONFLICT (job_key) DO UPDATE SET
                last_seen = greatest(j.last_seen, EXCLUDED.last_seen),
                description = coalesce(j.description, EXCLUDED.description),
                place = CASE WHEN EXCLUDED.place = 'Remote' THEN 'Remote' ELSE j.place END""",
            list(jobs.values()))
    conn.commit()
    print(f"transform: {len(postings)} postings -> {len(jobs)} jobs in scope")


def describe(conn) -> None:
    """Read the page of each new job that came without a description: its schema.org JobPosting
    (Greenhouse, Lever, Workday, Workable, LinkedIn's public page ...) or LinkedIn's description
    block. LinkedIn 5 seconds apart; a board that answers 429 waits for the next run."""
    todo = conn.execute(
        "SELECT job_id, source, job_url FROM core.job WHERE description IS NULL AND described_at IS NULL"
        " AND job_url LIKE 'http%%' AND first_seen >= current_date - %s ORDER BY role_rank, job_id LIMIT 300",
        (DAYS,)).fetchall()
    limited, found = set(), 0
    for job_id, source, url in todo:
        if source in limited:
            continue
        time.sleep(5 if source == "linkedin" else 1)
        try:
            r = requests.get(url, headers=BROWSER, timeout=30)
        except requests.RequestException as e:
            print(f"WARNING describe {url}: {e!r}"[:200])
            continue
        if r.status_code == 429:
            print(f"describe: {source} rate limit, the rest of {source} waits for the next run")
            limited.add(source)
            continue
        if r.status_code != 200:
            continue
        text = page_description(r.content)  # bytes: the page's own charset decides, not a guess
        found += text is not None
        conn.execute("UPDATE core.job SET description = %s, described_at = now() WHERE job_id = %s", (text, job_id))
        conn.commit()
    print(f"describe: {found} of {len(todo)} descriptions found")


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
    block = soup.select_one(".show-more-less-html__markup, .description__text")
    return block.get_text(" ", strip=True) if block else None


def score(conn) -> None:
    """Claude scores each new job 0-100 against the CV; skipped without ANTHROPIC_API_KEY or a CV.
    The description is cut at 6,000 characters to keep the cost down."""
    cv = ai.cv_block()
    if not (os.environ.get("ANTHROPIC_API_KEY") and cv):
        print("score: needs ANTHROPIC_API_KEY and cv.pdf, cv.md or cv.txt, skipped")
        return
    client = anthropic.Anthropic()
    todo = conn.execute(
        "SELECT job_id, title, company, location, place, description FROM core.job"
        " WHERE scored_at IS NULL AND emailed_at IS NULL ORDER BY role_rank, job_id LIMIT 400").fetchall()
    for job_id, title, company, location, place, description in todo:
        job = (f"Job: {title}\nCompany: {company}\nLocation: {location} ({place})\n\n"
               f"{(description or 'No description: judge from the title.')[:6000]}")
        try:
            fit = ai.fit(client, cv, job)
        except anthropic.AuthenticationError:
            raise
        except anthropic.APIError as e:
            print(f"WARNING score {job_id}: {e!r}"[:300])
            continue
        if fit:
            conn.execute("UPDATE core.job SET fit_score = %s, fit_reason = %s, scored_at = now() WHERE job_id = %s",
                         (max(0, min(100, fit.score)), fit.reason, job_id))
            conn.commit()
    print(f"score: {len(todo)} jobs")


def email(conn) -> None:
    """Email the window's jobs not emailed yet, best first: role rank, target companies, fit score.
    Without Gmail settings, write output/digest-<date>.html instead and mark nothing."""
    jobs = conn.cursor(row_factory=dict_row).execute(
        "SELECT * FROM core.job WHERE emailed_at IS NULL AND first_seen >= current_date - %s"
        " ORDER BY role_rank, target_company DESC, fit_score DESC NULLS LAST, date_posted DESC NULLS LAST",
        (DAYS,)).fetchall()
    if send(digest(jobs), len(jobs)):
        conn.execute("UPDATE core.job SET emailed_at = now() WHERE job_id = ANY(%s)", ([j["job_id"] for j in jobs],))
        conn.commit()


def digest(jobs: list[dict]) -> str:
    parts = [f"<p>{len(jobs)} new jobs, best match first. ⭐ = a target company. "
             'Track them at <a href="http://127.0.0.1:8501">http://127.0.0.1:8501</a>.</p>']
    for rank, label in ROLE_LABEL.items():
        group = [j for j in jobs if j["role_rank"] == rank]
        if not group:
            continue
        rows = "".join(
            f"<tr><td>{'' if j['fit_score'] is None else j['fit_score']}</td>"
            f"<td>{'⭐ ' if j['target_company'] else ''}<a href=\"{html.escape(j['job_url'])}\">{html.escape(j['title'])}</a></td>"
            f"<td>{html.escape(j['company'])}</td><td>{html.escape(j['location'])}</td><td>{j['place']}</td>"
            f"<td>{j['source']}</td><td>{j['date_posted'] or ''}</td><td>{html.escape(j['fit_reason'] or '')}</td></tr>"
            for j in group)
        parts.append(f"<h3>{rank}. {label} ({len(group)})</h3>"
                     '<table border="1" cellpadding="4" style="border-collapse:collapse">'
                     "<tr><th>Fit</th><th>Job</th><th>Company</th><th>Location</th><th>Where</th>"
                     f"<th>Source</th><th>Posted</th><th>Why</th></tr>{rows}</table>")
    return "\n".join(parts)


def send(body: str, count: int) -> bool:
    """Email the digest; without Gmail settings, write it to output/ instead and return False."""
    user, password = os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASSWORD")
    if not (user and password):
        (ROOT / "output").mkdir(exist_ok=True)
        path = ROOT / "output" / f"digest-{date.today()}.html"
        path.write_text(body, encoding="utf-8")
        print(f"email: no Gmail settings, wrote {path}")
        return False
    msg = EmailMessage()
    msg["Subject"] = f"Job radar {date.today()}: {count} new jobs"
    msg["From"] = user
    msg["To"] = os.environ.get("MAIL_TO") or user
    msg.set_content(f"{count} new jobs; open this email as HTML to see them.")
    msg.add_alternative(body, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(user, password.replace(" ", ""))
        smtp.send_message(msg)
    print(f"email: sent {count} jobs to {msg['To']}")
    return True
