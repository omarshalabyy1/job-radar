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
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from jobspy import scrape_jobs

from .config import (HOURS_OLD, KEYWORDS, PHENOM_SEARCHES, PLACES, REMOTE_OPEN_TO, RESPECT_ROBOTS, ROLES,
                     TANQEEB_PAGES, TANQEEB_SITES, place_of, role_of)

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


BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/129.0 Safari/537.36"}


def get(url: str, **params) -> requests.Response:
    r = requests.get(url, params=params, headers=BROWSER, timeout=60)
    r.raise_for_status()
    return r


def job_boards() -> list[dict]:
    """Indeed and Bayt through JobSpy, side by side: every role in every place, one board per call
    (one board down is a short day), 3 seconds apart on each board. LinkedIn jobs come only from
    your LinkedIn job-alert emails."""
    with ThreadPoolExecutor(2) as pool:
        return [r for rows in pool.map(board, ["indeed", "bayt"]) for r in rows]


def board(site: str) -> list[dict]:
    rows = []
    for _, label, term, _ in ROLES:
        for place, location, country in PLACES:
            time.sleep(3)
            try:
                df = scrape_jobs(site_name=site, search_term=term, location=location,
                                 country_indeed=country, hours_old=HOURS_OLD, results_wanted=30, verbose=0)
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


def workable_jobs() -> list[dict]:
    """Workable's public job search, across every company on Workable (startups and small and mid
    companies above all): each keyword in each place, posted in the last day, 4 searches at a time."""
    def search(args: tuple) -> list[dict]:
        keyword, (place, location, _) = args
        rows = []
        for j in get("https://jobs.workable.com/api/v1/jobs", query=keyword, location=location, day_range=1).json()["jobs"]:
            where = j.get("location") or {}
            location_text = ", ".join(filter(None, [where.get("city"), where.get("countryName")]))
            text = BeautifulSoup(f"{j.get('description', '')} {j.get('requirementsSection', '')}", "html.parser")
            if role_of(j["title"]) and place_of(location_text):
                rows.append(row("workable", "Remote" if j.get("workplace") == "remote" else None, j["title"],
                                (j.get("company") or {}).get("title"), location_text, j["url"], j["created"][:10],
                                text.get_text(" ", strip=True), j))
        return rows

    with ThreadPoolExecutor(4) as pool:
        return [r for rows in pool.map(search, [(k, p) for k in KEYWORDS for p in PLACES]) for r in rows]


def company_portals() -> list[dict]:
    """28,000+ companies' own career pages (Greenhouse, Lever, Ashby, Workday, BambooHR, iCIMS,
    Paylocity) from job-board-aggregator's daily crawl (by Riley Dorrington, data CC BY-NC 4.0):
    one ~75 MB download, 8 chunks at a time, instead of 28,000 calls. Kept: first seen in the
    window, a role, a place in scope."""
    def chunk(name: str) -> list[dict]:
        return json.loads(gzip.decompress(get(f"{PORTAL_FEED}/{name}").content))

    rows = []
    with ThreadPoolExecutor(8) as pool:
        for jobs in pool.map(chunk, get(f"{PORTAL_FEED}/jobs_manifest.json").json()["chunks"]):
            for j in jobs:
                if ((j.get("first_seen") or "")[:10] >= since() and j.get("title") and j.get("url")
                        and role_of(j["title"]) and place_of(j.get("location") or "")):
                    rows.append(row(j.get("ats", "portal").lower(), None, j["title"], j.get("company"),
                                    j["location"], j["url"], j["first_seen"][:10], None, j))
    return rows


def wuzzuf() -> list[dict]:
    """Wuzzuf, through the JSON API its own web app calls (robots.txt allows it; only its search
    page sits behind Cloudflare's challenge, and this does not touch it): every job posted in the
    last 24 hours, all of Egypt, all companies, 50 a page; then their details, 20 per call. The
    title rules keep yours."""
    api = {**BROWSER, "Content-Type": "application/vnd.api+json", "Accept": "application/vnd.api+json"}
    companies: dict[str, str] = {}
    start, total = 0, 1
    while start < total:
        body = {"startIndex": start, "pageSize": 50, "longitude": "0", "latitude": "0", "query": "",
                "searchFilters": {"post_date": ["within_24_hours"]}}
        r = requests.post("https://wuzzuf.net/api/search/job", data=json.dumps(body), headers=api, timeout=30)
        r.raise_for_status()
        found = r.json()
        for hit in found.get("data", []):
            fields = {f["name"]: f["value"] for f in hit["attributes"].get("computedFields", [])}
            companies.setdefault(hit["id"], (fields.get("company_name") or [""])[0].strip())
        start, total = start + 50, min(found.get("meta", {}).get("totalResultsCount", 0), 2000)
        time.sleep(1)
    ids, rows = list(companies), []
    for i in range(0, len(ids), 20):
        for job in requests.get("https://wuzzuf.net/api/job", params={"filter[other][ids]": ",".join(ids[i:i + 20])},
                                headers=api, timeout=30).json().get("data", []):
            a, where = job["attributes"], job["attributes"].get("location") or {}
            location = ", ".join(filter(None, [(where.get("city") or {}).get("name"), (where.get("country") or {}).get("name")]))
            if role_of(a["title"]) and place_of(location):
                text = BeautifulSoup(f"{a.get('description') or ''} {a.get('requirements') or ''}", "html.parser")
                posted = str(datetime.strptime(a["postedAt"], "%m/%d/%Y %H:%M:%S").date()) if a.get("postedAt") else None
                rows.append(row("wuzzuf", None, a["title"], companies[job["id"]], location, f"https://wuzzuf.net/{a['uri']}",
                                posted, text.get_text(" ", strip=True), job))
        time.sleep(1)
    return rows


def tanqeeb() -> list[dict]:
    """Tanqeeb, which gathers Wuzzuf, Bayt, Forasna, NaukriGulf, GulfTalent ... for Egypt and the
    Gulf: its job pages that robots.txt allows (TANQEEB_PAGES), newest first, each card's title,
    company, place, date and the board it came from."""
    def text(card, css: str) -> str:
        element = card.select_one(css)
        return element.get_text(" ", strip=True) if element else ""

    rows = []
    for site, place in TANQEEB_SITES.items():
        for page in TANQEEB_PAGES:
            soup = BeautifulSoup(get(f"https://{site}.tanqeeb.com/s/jobs/{page}", order_by="most_recent").text,
                                 "html.parser")
            for link in soup.select("a.search-job-title-link"):
                card = link.find_parent("div", class_="search-job-card-top").parent
                title, posted = link.get_text(" ", strip=True), tanqeeb_date(text(card, ".search-job-date"))
                if posted and str(posted) >= since() and role_of(title):
                    rows.append(row("tanqeeb", place, title, text(card, ".search-job-company-name"),
                                    text(card, ".search-job-workplace-location"),
                                    f"https://{site}.tanqeeb.com{link['href']}", str(posted), None,
                                    {"board": text(card, ".search-job-source"), "card": card.get_text(" | ", strip=True)}))
            time.sleep(1)
    return rows


def tanqeeb_date(text: str) -> date | None:
    """'6 hours ago', '2 days ago', 'yesterday' or '4 August 2026' as a date; None when unclear."""
    text = text.strip().lower()
    if m := re.match(r"(\d+)\s+(minute|hour|day)s?\s+ago", text):
        return date.today() - timedelta(days=int(m.group(1)) if m.group(2) == "day" else 0)
    if text == "yesterday":
        return date.today() - timedelta(days=1)
    try:
        return datetime.strptime(text, "%d %B %Y").date()
    except ValueError:
        return None


def company_sites(sites: list[tuple]) -> tuple[list[dict], list[tuple]]:
    """The career sites in core.company, side by side: (company, careers_url, platform, api) each.
    A site not yet known is first detected (platform, api); the rows come with each site's
    (platform, api, note, company), for core.company."""
    def read(site: tuple) -> tuple[list[dict], tuple]:
        company, url, platform, api = site
        note = None
        if platform in (None, "blocked", "forbidden", "unreachable"):
            platform, api, note = detect(url)
        if platform in ("blocked", "forbidden", "unreachable"):
            print(f"{company}: {platform} ({note})")
            return [], (platform, api, note, company)
        try:
            rows = READERS[platform](company, api)
        except Exception as e:  # one site down is a short day
            print(f"WARNING {company}: {e!r}"[:300])
            return [], (platform, api, f"read failed: {e!r}"[:200], company)
        print(f"{company} ({platform}): {len(rows)}")
        return rows, (platform, api, note, company)

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(read, sites))
    return [r for rows, _ in results for r in rows], [found for _, found in results]


# a careers page -> its platform: the platform's address in the page's URL or code
ATS_PATTERNS = [
    ("workable", r"(?:apply\.workable\.com/|workable\.com/api/v1/widget/accounts/)(?!api\b)([\w-]+)"),
    ("greenhouse", r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)"),
    ("lever", r"jobs\.lever\.co/([\w-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([\w.-]+)"),
]


def detect(url: str) -> tuple[str, str | None, str | None]:
    """(platform, what its reader calls, note) for a careers page: 'forbidden' when its robots.txt
    does not allow reading it, 'blocked' when it turns scripts away, 'page' when no known
    platform shows, even once Playwright has run the page's JavaScript (its links are read)."""
    parts = urlsplit(url)
    robots = RobotFileParser()
    try:
        robots.parse(requests.get(f"{parts.scheme}://{parts.netloc}/robots.txt", headers=BROWSER, timeout=20)
                     .text.splitlines())
    except requests.RequestException:
        pass
    if RESPECT_ROBOTS and not robots.can_fetch("*", url):
        return "forbidden", None, "its robots.txt forbids reading it"
    try:
        r = requests.get(url, headers=BROWSER, timeout=30)
    except requests.RequestException as e:
        return "blocked", None, f"unreachable: {e!r}"[:200]
    page = r.text
    if r.status_code in (401, 403, 429) or re.search(r"Just a moment|cf-chl-|challenge-platform|Access Denied", page[:20000]):
        return "blocked", None, f"it turns scripts away (HTTP {r.status_code})"
    if r.status_code >= 400:
        return "unreachable", None, f"page not found (HTTP {r.status_code}): check the careers URL"
    for platform, pattern in ATS_PATTERNS:
        if m := re.search(pattern, f"{url} {page}"):
            return platform, m.group(1), None
    if re.search(r"phenompeople|phApp\.", page):
        return "phenom", re.split(r"/search-results|/job/", url)[0].rstrip("/"), None
    if re.search(r"jobTitle-link|rmkcdn|successfactors", page):
        return "successfactors", f"{parts.scheme}://{parts.netloc}", None
    if page.lstrip()[:200].startswith(("<?xml", "<rss")):
        return "rss", url, None
    try:  # a job board its JavaScript loads (an embedded Greenhouse or Workable list ...)
        page = rendered(url)
    except Exception as e:
        return "page", url, f"could not render: {e!r}"[:200]
    for platform, pattern in ATS_PATTERNS:
        if m := re.search(pattern, page):
            return platform, m.group(1), None
    return "page", url, None


def workable(company: str, slug: str) -> list[dict]:
    """Every open job of a company on Workable, through its public widget API."""
    data = get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}").json()
    rows = []
    for j in data["jobs"]:
        location = ", ".join(filter(None, [j.get("city"), j.get("country")])) + (" (remote)" if j.get("telecommuting") else "")
        if role_of(j["title"]) and place_of(location):
            rows.append(row(company.lower(), None, j["title"], company, location, j["url"], j["published_on"], None, j))
    return rows


def greenhouse(company: str, slug: str) -> list[dict]:
    """Every open job of a company on Greenhouse, through its public job board API."""
    rows = []
    for j in get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs").json()["jobs"]:
        location = (j.get("location") or {}).get("name", "")
        if role_of(j["title"]) and place_of(location):
            rows.append(row(company.lower(), None, j["title"], company, location, j["absolute_url"],
                            (j.get("updated_at") or "")[:10] or None, None, j))
    return rows


def lever(company: str, slug: str) -> list[dict]:
    """Every open job of a company on Lever, through its public postings API."""
    rows = []
    for j in get(f"https://api.lever.co/v0/postings/{slug}", mode="json").json():
        location = (j.get("categories") or {}).get("location", "")
        if role_of(j["text"]) and place_of(location):
            posted = str(datetime.fromtimestamp(j["createdAt"] / 1000, timezone.utc).date()) if j.get("createdAt") else None
            rows.append(row(company.lower(), None, j["text"], company, location, j["hostedUrl"], posted,
                            j.get("descriptionPlain"), j))
    return rows


def ashby(company: str, slug: str) -> list[dict]:
    """Every open job of a company on Ashby, through its public job board API."""
    rows = []
    for j in get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}").json()["jobs"]:
        location = j.get("location", "") + (" (remote)" if j.get("isRemote") else "")
        if role_of(j["title"]) and place_of(location):
            rows.append(row(company.lower(), None, j["title"], company, location, j["jobUrl"],
                            (j.get("publishedAt") or "")[:10] or None, j.get("descriptionPlain"), j))
    return rows


def career_page(company: str, url: str) -> list[dict]:
    """A careers page on no known platform, rendered with Playwright: every link on it whose words
    are one of the roles. A job with no place on the page is taken as in Egypt (the companies you
    add are)."""
    soup = BeautifulSoup(rendered(url), "html.parser")
    rows, urls = [], set()
    for a in soup.find_all("a", href=True):
        title, job_url = a.get_text(" ", strip=True), urljoin(url, a["href"])
        if role_of(title) and job_url not in urls:
            urls.add(job_url)
            context = a.parent.get_text(" ", strip=True) if a.parent else ""
            rows.append(row(company.lower(), place_of(context) or "Egypt", title, company, context[:200], job_url,
                            None, None, {"title": title, "context": context[:500]}))
    return rows


def phenom(company: str, base: str) -> list[dict]:
    """A Phenom career site (Orange, DHL): its search page carries the jobs as JSON, with each
    job's skills; one search per keyword and place (PHENOM_SEARCHES), 1 second apart."""
    rows, urls = [], set()
    for search in PHENOM_SEARCHES:
        page = get(f"{base}/search-results", keywords=search).text
        start = page.find('"jobs":[')
        jobs = json.JSONDecoder().raw_decode(page, start + len('"jobs":'))[0] if start >= 0 else []
        for j in jobs:
            location = j.get("cityStateCountry") or f"{j.get('city', '')}, {j.get('country', '')}"
            url = f"{base}/job/{j['jobSeqNo']}"
            posted = (j.get("postedDate") or "")[:10]
            if url not in urls and posted >= since() and role_of(j.get("title", "")) and place_of(location):
                urls.add(url)
                rows.append(row(company.lower(), None, j["title"], company, location, url, posted,
                                f"{j.get('descriptionTeaser', '')} Skills: {', '.join(j.get('ml_skills', []))}", j))
        time.sleep(1)
    return rows


def successfactors(company: str, base: str) -> list[dict]:
    """A SuccessFactors career site (Nestlé): its newest jobs in each place, one page per place."""
    rows = []
    for _, location, _ in PLACES:
        soup = BeautifulSoup(get(f"{base}/search/", q="", locationsearch=location, sortColumn="referencedate",
                                 sortDirection="desc").text, "html.parser")
        for tr in soup.select("tr.data-row"):
            link, where, when = tr.select_one("a.jobTitle-link"), tr.select_one(".jobLocation"), tr.select_one(".jobDate")
            title, place_text = link.get_text(" ", strip=True), where.get_text(" ", strip=True) if where else ""
            try:
                posted = str(datetime.strptime(when.get_text(strip=True), "%b %d, %Y").date()) if when else ""
            except ValueError:
                posted = ""
            if posted >= since() and role_of(title) and place_of(place_text):
                rows.append(row(company.lower(), None, title, company, place_text, base + link["href"], posted, None,
                                {"title": title, "location": place_text, "date": posted}))
        time.sleep(1)
    return rows


def rss(company: str, feed: str) -> list[dict]:
    """A career site's RSS feed of its newest jobs (Deloitte Middle East on Avature)."""
    rows = []
    for item in ET.fromstring(get(feed).content).iter("item"):
        title, where, link = item.findtext("title", ""), item.findtext("description", ""), item.findtext("link", "")
        posted = str(parsedate_to_datetime(item.findtext("pubDate"))).split(" ")[0] if item.findtext("pubDate") else ""
        if (not posted or posted >= since()) and role_of(title) and place_of(f"{where} {title}"):
            rows.append(row(company.lower(), None, title, company, where, link, posted or None, None,
                            {"title": title, "location": where, "link": link}))
    return rows


def rendered(url: str) -> str:
    """The page's HTML after its JavaScript ran, in a headless Chromium (Playwright): for career
    pages that build their job list in the browser. It does not get past bot checks, and is not
    meant to."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(user_agent=BROWSER["User-Agent"])
            page.goto(url, wait_until="networkidle", timeout=45000)
            return page.content()
        finally:
            browser.close()


READERS = {"workable": workable, "greenhouse": greenhouse, "lever": lever, "ashby": ashby, "phenom": phenom,
           "successfactors": successfactors, "rss": rss, "page": career_page}


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
    """One link per job: a LinkedIn alert's tracking link as the plain job page, any other link
    without its utm_ tracking parameters (which differ from one email to the next)."""
    m = re.search(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)", url)
    if m:
        return f"https://www.linkedin.com/jobs/view/{m.group(1)}"
    parts = urlsplit(url)
    return urlunsplit(parts._replace(query=urlencode(
        [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith("utm_")])))
