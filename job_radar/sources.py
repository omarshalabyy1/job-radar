"""The job sources. Each returns rows for raw.job_posting, kept to the roles and places in scope.

No login anywhere: public search pages through JobSpy, public APIs and feeds. The worst a board
can do is rate-limit the run's internet address for a while, which costs that board's results
for the day and nothing else.
"""

from __future__ import annotations

import email
import email.policy
import gzip
import imaplib
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests
from bs4 import BeautifulSoup
from jobspy import scrape_jobs

from .config import HOURS_OLD, KEYWORDS, PLACES, REMOTE_OPEN_TO, ROLES, WORKABLE_ACCOUNTS, place_of, role_of

PORTAL_FEED = "https://feashliaa.github.io/job-board-data/data/chunks"
IMAP_HOSTS = {"gmail.com": "imap.gmail.com", "googlemail.com": "imap.gmail.com",
              "yahoo.com": "imap.mail.yahoo.com", "icloud.com": "imap.mail.me.com",
              "outlook.com": "outlook.office365.com", "hotmail.com": "outlook.office365.com",
              "live.com": "outlook.office365.com"}
# an email is opened only when its sender or its subject looks like a job
JOB_SENDERS = (r"jobalerts|jobs-listings|jobs-noreply|indeed|wuzzuf|bayt|glassdoor|naukrigulf|gulftalent"
               r"|akhtaboot|tanqeeb|forasna|jooble|himalayas|remotive|weworkremotely|wellfound|recruit"
               r"|talent|careers?@|hiring")
JOB_SUBJECTS = r"\bjobs?\b|vacanc|opening|opportunit|position|hiring|وظيف|وظائف"


def since() -> str:
    """The oldest posting date in the window, as YYYY-MM-DD."""
    return str(date.today() - timedelta(hours=HOURS_OLD))


def row(source, searched_for, title, company, location, job_url, date_posted, description, payload) -> dict:
    return {"source": source, "searched_for": searched_for, "title": title, "company": company or "",
            "location": location or "", "job_url": job_url, "date_posted": date_posted,
            "description": description, "payload": payload}


def get(url: str, **params) -> requests.Response:
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    return r


def job_boards() -> list[dict]:
    """LinkedIn, Indeed and Bayt through JobSpy: every role in every place, one board per call (one
    board down is a short day), plus LinkedIn's remote jobs open to someone in Egypt."""
    searches = [(place, location, country, False) for place, location, country in PLACES]
    searches.append(("Remote", "Egypt", "egypt", True))
    rows = []
    for _, label, term, _ in ROLES:
        for place, location, country, remote in searches:
            for site in ["linkedin"] if remote else ["linkedin", "indeed", "bayt"]:
                time.sleep(5)
                try:
                    df = scrape_jobs(site_name=site, search_term=term, location=location,
                                     country_indeed=country, is_remote=remote,
                                     hours_old=HOURS_OLD, results_wanted=30, verbose=0)
                except Exception as e:
                    print(f"WARNING {site} {label} / {place}: {e!r}"[:300])
                    continue
                print(f"{site} {label} / {place}: {len(df)}")
                for j in json.loads(df.to_json(orient="records", date_format="iso")):
                    rows.append(row(site, place, j["title"], j["company"], j["location"], j["job_url"],
                                    (j["date_posted"] or "")[:10] or None, j.get("description"), j))
    return rows


def himalayas() -> list[dict]:
    """Himalayas' search API: remote jobs open to someone in Egypt, newest first, per keyword."""
    rows = []
    for keyword in KEYWORDS:
        for offset in range(0, 100, 20):
            jobs = get("https://himalayas.app/jobs/api/search", q=keyword, country="EG", sort="recent",
                       offset=offset).json()["jobs"]
            for j in jobs:
                posted = str(datetime.fromtimestamp(int(j["pubDate"]), timezone.utc).date())
                if posted >= since() and role_of(j["title"]):
                    rows.append(row("himalayas", "Remote", j["title"], j["companyName"],
                                    ", ".join(j["locationRestrictions"]) or "Worldwide",
                                    j["applicationLink"], posted, j["description"], j))
            if not jobs or str(datetime.fromtimestamp(int(jobs[-1]["pubDate"]), timezone.utc).date()) < since():
                break
            time.sleep(1)
    return rows


def weworkremotely() -> list[dict]:
    """We Work Remotely's RSS feed of its latest jobs, kept when open to anywhere or EMEA."""
    rows = []
    for item in ET.fromstring(get("https://weworkremotely.com/remote-jobs.rss").content).iter("item"):
        f = {child.tag: child.text or "" for child in item}
        company, _, title = f["title"].partition(": ")
        posted = str(parsedate_to_datetime(f["pubDate"]).date())
        if posted >= since() and role_of(title) and re.search(REMOTE_OPEN_TO, f.get("region", ""), re.I):
            rows.append(row("weworkremotely", "Remote", title, company, f["region"], f["link"], posted,
                            f["description"], f))
    return rows


def workable() -> list[dict]:
    """Every open job of the companies in WORKABLE_ACCOUNTS, through Workable's public widget API."""
    rows = []
    for account in WORKABLE_ACCOUNTS:
        data = get(f"https://apply.workable.com/api/v1/widget/accounts/{account}").json()
        for j in data["jobs"]:
            location = ", ".join(filter(None, [j.get("city"), j.get("country")]))
            if j.get("telecommuting"):
                location += " (remote)"
            if role_of(j["title"]) and place_of(location):
                rows.append(row("workable", None, j["title"], data["name"], location, j["url"],
                                j["published_on"], None, j))
    return rows


def jooble() -> list[dict]:
    """Jooble's official API, which gathers the local boards it indexes; needs a free key in
    JOOBLE_API_KEY (https://jooble.org/api/about), skipped without one."""
    key = os.environ.get("JOOBLE_API_KEY")
    if not key:
        print("jooble: no JOOBLE_API_KEY, skipped")
        return []
    rows = []
    for keyword in KEYWORDS:
        for place, location, _ in PLACES:
            r = requests.post(f"https://jooble.org/api/{key}", json={"keywords": keyword, "location": location},
                              timeout=60)
            r.raise_for_status()
            for j in r.json().get("jobs", []):
                posted = (j.get("updated") or "")[:10]
                if posted >= since() and role_of(j["title"]):
                    rows.append(row("jooble", place, j["title"], j.get("company"), j.get("location"), j["link"],
                                    posted, j.get("snippet"), j))
            time.sleep(1)
    return rows


def company_portals() -> list[dict]:
    """28,000+ companies' own career pages (Greenhouse, Lever, Ashby, Workday, BambooHR, iCIMS,
    Paylocity) from job-board-aggregator's daily crawl (by Riley Dorrington, data CC BY-NC 4.0):
    one ~75 MB download a day instead of 28,000 calls. Kept: first seen in the window, a role, a
    place in scope."""
    rows = []
    for chunk in get(f"{PORTAL_FEED}/jobs_manifest.json").json()["chunks"]:
        for j in json.loads(gzip.decompress(get(f"{PORTAL_FEED}/{chunk}").content)):
            if ((j.get("first_seen") or "")[:10] >= since() and j.get("title") and j.get("url")
                    and role_of(j["title"]) and place_of(j.get("location") or "")):
                rows.append(row(j.get("ats", "portal").lower(), None, j["title"], j.get("company"),
                                j["location"], j["url"], j["first_seen"][:10], None, j))
    return rows


def mailboxes(seen: set[str]) -> list[dict]:
    """Job alerts (LinkedIn, Indeed, Wuzzuf, Bayt ...) from your inboxes, over IMAP and read-only:
    nothing is marked read, moved or deleted, and nothing leaves the laptop. Only an email whose
    sender or subject looks like a job is opened. An email already read (its Message-ID in `seen`)
    is not read again.

    MAILBOXES in .env: address:app-password[:imap-host], comma-separated; the host is known for
    Gmail, Yahoo, iCloud and Outlook addresses."""
    accounts = [a.strip() for a in os.environ.get("MAILBOXES", "").split(",") if a.strip()]
    if not accounts:
        print("email: no MAILBOXES, skipped")
        return []
    rows = []
    for account in accounts:
        address, password, *host = account.split(":")
        try:
            rows += read_mailbox(address, password.replace(" ", ""),
                                 host[0] if host else IMAP_HOSTS[address.split("@")[-1].lower()], seen)
        except Exception as e:  # one mailbox down is a short day
            print(f"WARNING email {address}: {e!r}"[:300])
    return rows


def read_mailbox(address: str, password: str, host: str, seen: set[str]) -> list[dict]:
    rows = []
    with imaplib.IMAP4_SSL(host) as imap:
        imap.login(address, password)
        imap.select("INBOX", readonly=True)
        since_day = (date.today() - timedelta(hours=HOURS_OLD)).strftime("%d-%b-%Y")
        for uid in imap.uid("search", None, "SINCE", since_day)[1][0].split():
            head = imap.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT MESSAGE-ID DATE)])")[1][0][1]
            h = email.message_from_bytes(head, policy=email.policy.default)
            message_id = str(h["Message-ID"] or f"{address}/{uid.decode()}")
            if message_id in seen or not (re.search(JOB_SENDERS, str(h["From"]), re.I)
                                          or re.search(JOB_SUBJECTS, str(h["Subject"]), re.I)):
                continue
            msg = email.message_from_bytes(imap.uid("fetch", uid, "(BODY.PEEK[])")[1][0][1],
                                           policy=email.policy.default)
            body = msg.get_body(preferencelist=("html",))
            jobs = jobs_in_email(body.get_content()) if body else []
            posted = str(parsedate_to_datetime(str(h["Date"])).date()) if h["Date"] else None
            meta = {"mailbox": address, "message_id": message_id, "from": str(h["From"]), "subject": str(h["Subject"])}
            for title, company, location, url in jobs:
                # an alert you subscribed to is for your region unless its location says otherwise
                rows.append(row("email", place_of(location) or "Egypt", title, company, location, url, posted,
                                None, {**meta, "title": title, "company": company, "location": location, "url": url}))
            print(f"email {address}: {h['Subject']!s:.60} -> {len(jobs)} jobs")
    return rows


def jobs_in_email(page: str) -> list[tuple[str, str, str, str]]:
    """(title, company, location, url) of each link in a job email whose words are one of the roles.
    Job alerts put the company and location on the line under the title ("Company · Location")."""
    soup = BeautifulSoup(page, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        links.append((a.get_text(" ", strip=True), a["href"]))
        a.replace_with(f"\n@@{len(links) - 1}@@\n")
    lines = [line.strip() for line in soup.get_text("\n").splitlines() if line.strip()]
    jobs, urls = [], set()
    for i, line in enumerate(lines):
        link = re.fullmatch(r"@@(\d+)@@", line)
        if not link:
            continue
        title, url = links[int(link.group(1))]
        url = canonical_url(url)
        if not role_of(title) or url in urls:
            continue
        urls.add(url)
        after = lines[i + 1] if i + 1 < len(lines) else ""
        if (next_link := re.fullmatch(r"@@(\d+)@@", after)):  # the company is a link of its own
            after = links[int(next_link.group(1))][0]
        company, location = (re.split(r"\s+[·•|–-]\s+", after, maxsplit=1) + [""])[:2]
        if not location and i + 2 < len(lines) and not lines[i + 2].startswith("@@"):
            location = lines[i + 2]  # the location on a line of its own
        jobs.append((title, company if not role_of(company) else "", location, url))
    return jobs


def canonical_url(url: str) -> str:
    """A LinkedIn alert's tracking link as the plain job page; any other link as given."""
    m = re.search(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)", url)
    return f"https://www.linkedin.com/jobs/view/{m.group(1)}" if m else url
