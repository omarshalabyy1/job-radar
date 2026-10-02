"""job_radar: the day's new data and AI jobs in Egypt, the Gulf and remote, in one email.

Searches LinkedIn, Indeed and Bayt through JobSpy, and Remotive's public API, as a guest: no
login anywhere, so there is no account to ban. The worst a job board can do is rate-limit the
run's internet address for a while (LinkedIn answers HTTP 429), which costs that board's results
for the day and nothing else. Searches are spaced a few seconds apart and stay small.

Each job is ranked by its title, first match wins, in this order: 1 AI & data engineer,
2 data engineer, 3 AI engineer (NLP, LLM, agents, RAG), 4 BI developer, 5 data analyst. A title
that matches none, or says senior, lead, manager and the like, is dropped.

A job already emailed (same title and company, on any board) is not emailed again: data/seen.txt
keeps them, and gets the new ones only after the email is sent. Searches look back 72 hours, so a
day the laptop slept through is caught up by the next run.

    python job_radar.py    # GMAIL_USER + GMAIL_APP_PASSWORD set: emails MAIL_TO (default GMAIL_USER)
                           # not set: writes output/digest-<date>.html, data/seen.txt untouched
"""

from __future__ import annotations

import html
import os
import re
import smtplib
import time
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path

import pandas as pd
import requests
from jobspy import scrape_jobs

HERE = Path(__file__).resolve().parent
SEEN = HERE / "data" / "seen.txt"
OUTPUT = HERE / "output"
HOURS_OLD = 72

# (rank, label, search term, title patterns that must all match)
ROLES = [
    (1, "AI & Data Engineer", '"AI Data Engineer" OR "Data AI Engineer"',
     [r"\bdata\b", r"\b(ai|ml|genai|llm|machine learning)\b", r"engineer|developer"]),
    (2, "Data Engineer", '"Data Engineer" OR "ETL Developer" OR "Analytics Engineer"',
     [r"data engineer|\betl\b|big data|data platform|analytics engineer|data warehouse|\bdwh\b"]),
    (3, "AI Engineer (NLP, LLM, agents, RAG)",
     '"AI Engineer" OR "LLM Engineer" OR "NLP Engineer" OR "Machine Learning Engineer" OR "GenAI Engineer"',
     [r"\b(ai|ml|nlp|llms?|genai|gen ai|generative ai|machine learning|deep learning|rag|agentic)\b",
      r"engineer|developer"]),
    (4, "BI Developer", '"BI Developer" OR "Power BI" OR "Business Intelligence"',
     [r"\bbi\b|business intelligence|power ?bi|tableau"]),
    (5, "Data Analyst", '"Data Analyst" OR "Reporting Analyst"',
     [r"(data|business|reporting|insights?) analyst|data analytics"]),
]
TOO_SENIOR = r"\b(senior|sr|lead|principal|staff|head|director|manager|vp|chief|architect)\b"

# (label, location, Indeed country, remote only); Remote = LinkedIn's remote jobs open to Egypt
PLACES = [
    ("Egypt", "Egypt", "egypt", False),
    ("UAE", "United Arab Emirates", "united arab emirates", False),
    ("Saudi Arabia", "Saudi Arabia", "saudi arabia", False),
    ("Qatar", "Qatar", "qatar", False),
    ("Remote", "Egypt", "egypt", True),
]
REMOTIVE_OPEN_TO = r"worldwide|anywhere|emea|africa|middle east|egypt"


def search() -> pd.DataFrame:
    """Every role in every place on every board, one board per call: one board down is a short day."""
    frames = []
    for _, label, term, _ in ROLES:
        for place, location, country, remote in PLACES:
            for site in ["linkedin"] if remote else ["linkedin", "indeed", "bayt"]:
                try:
                    df = scrape_jobs(site_name=site, search_term=term, location=location,
                                     country_indeed=country, is_remote=remote,
                                     hours_old=HOURS_OLD, results_wanted=30, verbose=0)
                    print(f"{label} / {place} / {site}: {len(df)}")
                    frames.append(df.assign(where=place))
                except Exception as e:
                    print(f"WARNING {label} / {place} / {site}: {e!r}"[:300])
                time.sleep(5)
    frames.append(remotive())
    return pd.concat(frames, ignore_index=True)


def remotive() -> pd.DataFrame:
    """Remotive's public API: one call a day returns every listed remote job."""
    try:
        jobs = pd.DataFrame(requests.get("https://remotive.com/api/remote-jobs", timeout=60).json()["jobs"])
    except Exception as e:
        print(f"WARNING remotive: {e!r}"[:300])
        return pd.DataFrame()
    posted = pd.to_datetime(jobs["publication_date"]).dt.date
    jobs = jobs[jobs["candidate_required_location"].str.contains(REMOTIVE_OPEN_TO, case=False, na=False)
                & (posted >= date.today() - timedelta(hours=HOURS_OLD))]
    print(f"remotive: {len(jobs)}")
    return pd.DataFrame({"site": "remotive", "title": jobs["title"], "company": jobs["company_name"],
                         "location": jobs["candidate_required_location"], "date_posted": posted,
                         "job_url": jobs["url"], "where": "Remote"})


def rank(title: str) -> int | None:
    t = title.lower()
    if re.search(TOO_SENIOR, t):
        return None
    for r, _, _, patterns in ROLES:
        if all(re.search(p, t) for p in patterns):
            return r
    return None


def digest(jobs: pd.DataFrame) -> str:
    parts = [f"<p>{len(jobs)} new jobs posted in the last {HOURS_OLD // 24} days, best match first.</p>"]
    for r, label, _, _ in ROLES:
        group = jobs[jobs["rank"] == r]
        if group.empty:
            continue
        rows = "".join(
            f'<tr><td><a href="{html.escape(j.job_url)}">{html.escape(j.title)}</a></td>'
            f"<td>{html.escape(j.company)}</td><td>{html.escape(j.location)}</td>"
            f"<td>{j.where}</td><td>{j.site}</td><td>{'' if pd.isna(j.posted) else j.posted.date()}</td></tr>"
            for j in group.itertuples())
        parts.append(f"<h3>{r}. {label} ({len(group)})</h3>"
                     '<table border="1" cellpadding="4" style="border-collapse:collapse">'
                     "<tr><th>Job</th><th>Company</th><th>Location</th><th>Where</th>"
                     f"<th>Board</th><th>Posted</th></tr>{rows}</table>")
    return "\n".join(parts)


def send(body: str, count: int) -> bool:
    """Email the digest; without Gmail settings, write it to output/ instead and return False."""
    user, password = os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASSWORD")
    if not (user and password):
        OUTPUT.mkdir(exist_ok=True)
        path = OUTPUT / f"digest-{date.today()}.html"
        path.write_text(body, encoding="utf-8")
        print(f"no Gmail settings: wrote {path}")
        return False
    msg = EmailMessage()
    msg["Subject"] = f"Job radar {date.today()}: {count} new jobs"
    msg["From"] = user
    msg["To"] = os.environ.get("MAIL_TO") or user
    msg.set_content(f"{count} new jobs; open this email as HTML to see them.")
    msg.add_alternative(body, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)
    print(f"emailed {count} jobs to {msg['To']}")
    return True


def main() -> None:
    jobs = search()
    jobs = jobs.dropna(subset=["title", "job_url"]).fillna({"company": "", "location": ""})
    jobs["rank"] = jobs["title"].map(rank)
    jobs = jobs.dropna(subset=["rank"])
    jobs["posted"] = pd.to_datetime(jobs["date_posted"], errors="coerce")
    jobs["key"] = jobs["title"].str.lower().str.strip() + " | " + jobs["company"].str.lower().str.strip()
    jobs = (jobs.sort_values(["rank", "posted"], ascending=[True, False], na_position="last")
                .drop_duplicates("job_url").drop_duplicates("key"))
    seen = set(SEEN.read_text(encoding="utf-8").splitlines()) if SEEN.exists() else set()
    new = jobs[~jobs["key"].isin(seen)]
    if send(digest(new), len(new)):
        SEEN.parent.mkdir(exist_ok=True)
        with SEEN.open("a", encoding="utf-8") as f:
            f.writelines(k + "\n" for k in new["key"])


if __name__ == "__main__":
    main()
