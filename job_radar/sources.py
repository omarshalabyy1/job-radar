"""The job sources. Each returns rows for raw.job_posting, kept to the roles and places in scope.

Every public page is read, whatever a site's robots.txt or terms say (your rule, 2026-10-05: you
take the risk), as a browser asks for it: a site that turns requests away is asked again with a
real Chrome's TLS handshake (request()). The code never logs in and never gets past a bot check: a
site that shows one is opened by you once (scripts/open_blocked.py) and read with your saved
session from then on. Every source stays inside its limits, so no board has a reason to block the
laptop's internet address: see fetch() (a host that answers 429 is left alone for as long as it
asks) and the README's limits. LinkedIn is never opened: its jobs come from your alert emails.
"""

from __future__ import annotations

import email
import email.policy
import gzip
import imaplib
import json
import logging
import os
import random
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as chrome_requests  # requests' API, with a real Chrome's TLS handshake
from jobspy import scrape_jobs

from .config import (BAYT_PLACES, EXTRACT_SECONDS, HOURS_OLD, KEYWORDS, ONSITE_PLACES, PHENOM_SEARCHES, PLACES,
                     RELOMOTE_PAGES, REMOTE, REMOTE_OPEN_TO, ROLES, ROOT, TANQEEB_PAGES, TANQEEB_SITES, place_of,
                     role_of)

STARTED = time.monotonic()  # each step is its own process: its time budget counts from here


def time_left(budget: float) -> float:
    return STARTED + budget - time.monotonic()

PORTAL_FEED = "https://feashliaa.github.io/job-board-data/data/chunks"
IMAP_HOSTS = {"gmail.com": "imap.gmail.com", "googlemail.com": "imap.gmail.com",
              "yahoo.com": "imap.mail.yahoo.com", "icloud.com": "imap.mail.me.com",
              "outlook.com": "outlook.office365.com", "hotmail.com": "outlook.office365.com",
              "live.com": "outlook.office365.com"}
# Every email is read. One from a job site gives every link titled like one of your roles (any
# LinkedIn email: only its job links); any other email (a recruiter, a newsletter) only its links to
# a job page, so an article such as "10 KPIs Every Data Analyst..." is no job, whatever the subject.
JOB_SENDERS = (r"linkedin|jobalerts|jobs-listings|jobs-noreply|indeed|wuzzuf|bayt|glassdoor|naukrigulf|gulftalent"
               r"|akhtaboot|tanqeeb|forasna|jooble|himalayas|remotive|weworkremotely|wellfound|recruit"
               r"|talent|careers?@|hiring")
JOB_PAGE = (r"/jobs?/|/careers?/|/vacanc|/positions?/|viewjob|job-?listing|[?&]jk=|greenhouse\.io|lever\.co"
            r"|ashbyhq\.com|workable\.com")
# a link that only says "Learn more" under a job card (Wellfound): the card is the text above it
GENERIC_LINK = r"learn more|view job|see job|view details|more details|apply|apply now|see more"
# LinkedIn's job alert senders: every job in their emails is kept, whatever its title
LINKEDIN_ALERTS = r"(jobalerts-noreply|jobs-listings)@linkedin\.com"
# an email bigger than this carries attachments, not job alerts: skipped, to spare Gmail's daily
# IMAP download allowance (2,500 MB)
MAIL_BYTES = 5_000_000
# the feed's last_updated when it was last read: it is downloaded again only once it changes
FEED_READ = ROOT / "output" / "feed-last-updated.txt"


def since() -> str:
    """The oldest posting date in the window, as YYYY-MM-DD."""
    return str(date.today() - timedelta(hours=HOURS_OLD))


def week() -> str:
    """The oldest posting date for the boards whose dates run days late, as YYYY-MM-DD: a job already
    stored is not stored twice, so the longer look back only catches what they publish late."""
    return str(date.today() - timedelta(days=7))


def row(source, searched_for, title, company, location, job_url, date_posted, description, payload) -> dict:
    return {"source": source, "searched_for": searched_for, "title": title, "company": company or "",
            "location": location or "", "job_url": job_url, "date_posted": date_posted,
            "description": description, "payload": payload}


BROWSERS = threading.local()  # one headless Chromium per worker thread, see rendered()
BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/129.0 Safari/537.36"}


# Every request a source makes goes through fetch(). A host that answers 429 Too Many Requests (or 503
# with a Retry-After) is asked nothing more, by any step or run, until the time it gives (an hour
# when it gives none); one that still answers 401 or 403 to a real Chrome's handshake, for 6 hours;
# a host can also be held on our side (Workable: one round a day). One file per host in output/waits/
# holds the time, as the extract steps are separate processes side by side.
WAITS = ROOT / "output" / "waits"
# Seconds between two requests to one host, across a step's threads (request()): 1, unless named here.
# Workable's search is limited by the day, not the second (one round a day); the 28,000-company feed
# is files on GitHub's servers.
GAP = {"jobs.workable.com": 0.25, "feashliaa.github.io": 0.0}
NEXT_TURN: dict[str, float] = {}
TURNS = threading.Lock()
CHROME_ONLY: set[str] = set()  # hosts that answered only curl_cffi in this step (NaukriGulf hangs plain requests 15 s)


class Held(Exception):
    """A host not to be asked yet."""


class Challenge(Exception):
    """A page that shows a bot check, or a site that drops the headless browser."""


def waiting(host: str) -> float:
    """Seconds left before `host` may be asked again; 0 when it may be asked now."""
    try:
        return max(0.0, float((WAITS / host).read_text()) - time.time())
    except (OSError, ValueError):
        return 0.0


def hold(host: str, seconds: float) -> None:
    """Ask `host` nothing for `seconds`, in every step and run."""
    WAITS.mkdir(parents=True, exist_ok=True)
    (WAITS / host).write_text(str(time.time() + seconds))


def retry_after(value: str | None) -> float:
    """A Retry-After header in seconds (it gives seconds or a date); an hour when there is none."""
    if value and value.strip().isdigit():
        return float(value)
    try:
        return max(60.0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return 3600.0


def duration(seconds: float) -> str:
    return f"{seconds / 60:.0f} minutes" if seconds < 3600 else f"{seconds / 3600:.1f} hours"


# A site you opened by hand (scripts/open_blocked.py): its cookies and that browser's user agent
SESSIONS = ROOT / "output" / "sessions"
# A request that failed, from either client
ERRORS = (requests.RequestException, chrome_requests.exceptions.RequestException)
# A bot check's own page, not the passive Cloudflare script many ordinary pages carry
CHALLENGE = r"<title>(Just a moment|Access Denied|Attention Required)|cf-chl-"


def session(host: str) -> dict | None:
    """The session saved for `host` (user_agent, cookies), or None."""
    try:
        return json.loads((SESSIONS / f"{host}.json").read_text())
    except (OSError, ValueError):
        return None


def pace(host: str) -> None:
    """Wait for `host`'s turn: GAP seconds after the request before it, whichever thread made it."""
    with TURNS:
        turn = max(time.monotonic(), NEXT_TURN.get(host, 0.0))
        NEXT_TURN[host] = turn + GAP.get(host, 1.0)
    time.sleep(max(0.0, turn - time.monotonic()))


def request(method: str, url: str, **kwargs):
    """One request, as a browser makes it, at the host's pace: with requests first, and when the site
    turns it away (401, 403), drops the connection or never answers, once more with curl_cffi, whose
    TLS handshake is a real Chrome's - the check many bot filters make (it opened 5 of the 6 career
    sites that refused requests, 2026-10-05; Bain is the other way round, hence requests first). Both
    tries together keep to the caller's timeout: requests gets half, curl_cffi what is left. A site
    you opened by hand gets your session's cookies and user agent."""
    host = urlsplit(url).hostname
    pace(host)
    headers = {**BROWSER, **kwargs.pop("headers", {})}
    if saved := session(host):
        headers["User-Agent"] = saved["user_agent"]
        kwargs["cookies"] = {c["name"]: c["value"] for c in saved["cookies"]}
    timeout, started = kwargs.pop("timeout", 60), time.monotonic()
    if host not in CHROME_ONLY:
        try:
            r = requests.request(method, url, headers=headers, timeout=timeout / 2, **kwargs)
            if r.status_code not in (401, 403):
                return r
        except (requests.Timeout, requests.ConnectionError):  # ConnectionError covers an SSL handshake refused
            pass
        pace(host)
    if not saved:  # curl_cffi sends the user agent of the Chrome it imitates
        del headers["User-Agent"]
    r = chrome_requests.request(method, url, headers=headers, impersonate="chrome",
                                timeout=max(1.0, timeout - (time.monotonic() - started)), **kwargs)
    if r.status_code < 400:  # only Chrome gets through: this step asks it as Chrome from now on
        CHROME_ONLY.add(host)
    return r


def fetch(method: str, url: str, hold_on_403: bool = True, **kwargs):
    """hold_on_403=False for a single job page (describe): its host can be a platform's API too
    (apply.workable.com), which one refused page must not stop for 6 hours."""
    host = urlsplit(url).hostname
    if left := waiting(host):
        raise Held(f"{host} is held for {duration(left)} more")
    r = request(method, url, **kwargs)
    if r.status_code == 429 or (r.status_code == 503 and "Retry-After" in r.headers):
        hold(host, retry_after(r.headers.get("Retry-After")))
        raise Held(f"{host} answered {r.status_code}: held for {duration(waiting(host))}")
    if r.status_code in (401, 403):  # turned away even as a real Chrome: asking again soon risks a ban
        if hold_on_403:
            hold(host, 6 * 3600)
        raise Held(f"{host} answered {r.status_code}" + (": held for 6 hours" if hold_on_403 else ""))
    r.raise_for_status()
    return r


def get(url: str, **params):
    return fetch("GET", url, params=params)


class Blocked(logging.Handler):
    """JobSpy logs a board's 403 or 429 and returns no jobs: this notes which board it was."""
    def __init__(self) -> None:
        super().__init__()
        self.boards: set[str] = set()

    def emit(self, record: logging.LogRecord) -> None:
        if re.search(r"status code (403|429)", record.getMessage()):
            self.boards.add(record.name.split(":")[-1].lower())


BLOCKED = Blocked()
for _board in ("Indeed", "Bayt"):
    logging.getLogger(f"JobSpy:{_board}").addHandler(BLOCKED)


def job_boards() -> list[dict]:
    """Indeed and Bayt through JobSpy: every role in every place, remote jobs only outside Egypt
    (ONSITE_PLACES), one board and place per call (one down is a short day). Indeed searches 6
    places at a time; Bayt, which has jobs only in Egypt and the Gulf, one search at a time. 3
    seconds between a call's searches. A board that answers 403 or 429 gets no more searches for
    6 hours (JobSpy gives no Retry-After). LinkedIn jobs come only from your LinkedIn job-alert
    emails. Searches stop at the step's time budget; Bayt's places after Egypt come in a new order
    each run, so none is always the one cut."""
    bayt_places = [p for p in PLACES if p[0] in BAYT_PLACES]
    bayt_places = bayt_places[:1] + random.sample(bayt_places[1:], len(bayt_places) - 1)
    with ThreadPoolExecutor(7) as pool:
        bayt = pool.submit(board, "bayt", bayt_places)
        indeed = pool.map(lambda place: board("indeed", [place]), PLACES)
        return [r for rows in [*indeed, bayt.result()] for r in rows]


def board(site: str, places: list[tuple]) -> list[dict]:
    rows = []
    for place, location, country in places:
        for _, label, term, _ in ROLES:
            if site in BLOCKED.boards or waiting(site):
                print(f"{site}: held for {duration(waiting(site))} more, the rest waits")
                return rows
            if time_left(EXTRACT_SECONDS) < 15:  # room left for one more search and the load
                print(f"{site}: time budget reached, the rest waits for the next run")
                return rows
            time.sleep(3)
            try:
                df = scrape_jobs(site_name=site, search_term=term, location=location, country_indeed=country,
                                 is_remote=place not in ONSITE_PLACES, hours_old=HOURS_OLD, results_wanted=30,
                                 verbose=0)
            except Exception as e:
                print(f"WARNING {site} {label} / {location}: {e!r}"[:300])
                continue
            if site in BLOCKED.boards:  # its 403 or 429: leave it alone, this run and the next
                hold(site, 6 * 3600)
                print(f"{site} answered 403 or 429: held for 6 hours")
                return rows
            print(f"{site} {label} / {location}: {len(df)}")
            for j in json.loads(df.to_json(orient="records", date_format="iso")):
                rows.append(row(site, place, j["title"], j["company"], j["location"], j["job_url"],
                                (j["date_posted"] or "")[:10] or None, j.get("description"), j))
    return rows


def himalayas() -> list[dict]:
    """Himalayas' search API: remote jobs open to someone in Egypt, newest first, per keyword."""
    rows = []
    for keyword in KEYWORDS:
        for offset in range(0, 100, 20):
            try:
                jobs = get("https://himalayas.app/jobs/api/search", q=keyword, country="EG", sort="recent",
                           offset=offset).json()["jobs"]
            except Held as e:  # keep what the searches before it found
                print(f"himalayas: {e}")
                return rows
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


def remote_board(source: str, jobs: list, title, company, where, url, posted, description) -> list[dict]:
    """A remote job board's feed as rows: one of your roles, from a place in scope (anywhere,
    Europe, the Gulf ... once marked remote), posted in the last 7 days - these boards publish a day
    or more late (week()). The field names are the board's."""
    rows = []
    for j in jobs:
        location = f"{j.get(where) or ''} (remote)"
        day = str(datetime.fromtimestamp(int(j[posted]), timezone.utc).date()) if str(j.get(posted, "")).isdigit() \
            else str(j.get(posted) or "")[:10]
        if day >= week() and j.get(title) and j.get(url) and role_of(j[title]) and place_of(location):
            rows.append(row(source, None, j[title], j.get(company), location, j[url], day, j.get(description), j))
    return rows


def remotive() -> list[dict]:
    """Remotive's public API, 24 hours late. It asks for at most 4 calls a day: held 6 hours after each."""
    try:
        jobs = get("https://remotive.com/api/remote-jobs").json()["jobs"]
    except Held as e:
        print(f"remotive: {e}")
        return []
    hold("remotive.com", 6 * 3600)
    return remote_board("remotive", jobs, "title", "company_name", "candidate_required_location", "url",
                        "publication_date", "description")


def remoteok() -> list[dict]:
    """Remote OK's public API (its first item is its terms, not a job)."""
    jobs = [j for j in get("https://remoteok.com/api").json() if j.get("position")]
    return remote_board("remoteok", jobs, "position", "company", "location", "url", "epoch", "description")


def jobicy() -> list[dict]:
    """Jobicy's public API: its 100 newest remote jobs."""
    jobs = get("https://jobicy.com/api/v2/remote-jobs", count=100).json()["jobs"]
    return remote_board("jobicy", jobs, "jobTitle", "companyName", "jobGeo", "url", "pubDate", "jobDescription")


def workingnomads() -> list[dict]:
    """Working Nomads' public API, a few days late."""
    jobs = get("https://www.workingnomads.com/api/exposed_jobs/").json()
    return remote_board("workingnomads", jobs, "title", "company_name", "location", "url", "pub_date", "description")


def arbeitnow() -> list[dict]:
    """Arbeitnow's public API (Europe): its newest page, remote jobs only."""
    jobs = [j for j in get("https://www.arbeitnow.com/api/job-board-api").json()["data"] if j.get("remote")]
    return remote_board("arbeitnow", jobs, "title", "company_name", "location", "url", "created_at", "description")


def remoteco(seen: set[str]) -> list[dict]:
    """Remote.co: its search pages sit behind a bot check, so its sitemap of the latest jobs (about 500)
    is read, and only the pages of jobs titled like your roles and not stored before (`seen`): fully
    remote, from a place in scope, posted in the last week. Remote.co answers only a real Chrome."""
    sitemap = get("https://remote.co/latest-jobs-sitemap.xml").text
    rows = []
    for url in re.findall(r"<loc>(https://remote\.co/job-details/[^<]+)</loc>", sitemap):
        words = re.sub(r"-[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$", "", url.rsplit("/", 1)[1]).replace("-", " ")
        if url in seen or not role_of(words):
            continue
        if time_left(EXTRACT_SECONDS) < 15:
            print("remoteco: time budget reached, the rest waits for the next run")
            break
        try:
            page = BeautifulSoup(get(url).text, "html.parser").find("script", id="__NEXT_DATA__")
        except Held as e:  # keep what the pages before it found
            print(f"remoteco: {e}")
            break
        j = json.loads(page.string)["props"]["pageProps"]["jobDetails"] if page else {}
        location = f"{', '.join(j.get('countries') or j.get('allowedCandidateLocation') or ['Worldwide'])} (remote)"
        if (j.get("remoteWorkLevel") == "100REMWORK" and (j.get("postedDate") or "")[:10] >= week()
                and role_of(j.get("title") or "") and place_of(location)):
            rows.append(row("remoteco", None, j["title"], (j.get("company") or {}).get("name"), location, url,
                            j["postedDate"][:10], None, {k: v for k, v in j.items() if k != "company"}))
    return rows


def dailyremote() -> list[dict]:
    """DailyRemote: its newest remote jobs in your fields, 30 a page, until a page reaches past the
    window (about 2 pages a run). It hides the company behind its paid plan, so none is stored."""
    roles = ("data-engineer,data-scientist,data-analyst,ai-engineer,machine-learning-engineer,analytics-engineer,"
             "business-intelligence-developer,llm-engineer")
    rows = []
    for n in range(1, 6):
        try:
            soup = BeautifulSoup(get("https://dailyremote.com/remote-jobs", roles=roles, sort_by="time", page=n).text,
                                 "html.parser")
        except Held as e:  # keep what the pages before it found
            print(f"dailyremote: {e}")
            break
        cards, past_window = soup.select("article.lst-card"), False
        for card in cards:
            link, pill = card.select_one(".lst-card__title a"), card.select_one(".lst-card__meta .lst-pill")
            byline = card.select(".lst-card__byline span")
            posted = tanqeeb_date(byline[-1].get_text(strip=True)) if byline else None
            if not link or not posted or str(posted) < since():
                past_window = past_window or bool(posted)
                continue
            where = (pill.get("title") or pill.get_text(strip=True)) if pill else "Worldwide"
            title = link.get_text(strip=True)
            if role_of(title) and place_of(f"{where} (remote)"):
                rows.append(row("dailyremote", None, title, "", f"{where} (remote)",
                                urljoin("https://dailyremote.com", link["href"]), str(posted), None,
                                {"card": card.get_text(" | ", strip=True)}))
        if past_window or not cards:
            break
    return rows


def relomote() -> list[dict]:
    """Relomote: remote jobs from companies' own career pages, each checked for the countries it can
    hire from. Its pages of data and engineering jobs open to Egypt (RELOMOTE_PAGES), newest first,
    15 a page, until a page reaches past the window (about 3 pages a run); 1 second apart."""
    rows = []
    for name in RELOMOTE_PAGES:
        for n in range(1, 11):
            time.sleep(1)
            try:
                page = get(f"https://relomote.com/remote-jobs/{name}", **({"page": n} if n > 1 else {})).text
            except Held as e:  # keep what the pages before it found
                print(f"relomote: {e}")
                return rows
            cards, past_window = BeautifulSoup(page, "html.parser").find_all("article"), False
            for card in cards:
                link, stamp = card.find("a", href=re.compile(r"^/jobs/")), card.find("time")
                if not (link and stamp and stamp.get("datetime")):
                    continue
                posted = stamp["datetime"][:10]
                if posted < since():
                    past_window = True
                    continue
                # the card's words: title, company, field, who it hires from ("Egypt", "Worldwide"), mode, salary, age
                words = card.get_text("|", strip=True).split("|")
                mode = next((w for w in words if w in ("Remote", "Hybrid", "On-site", "Onsite")), "")
                where = words[words.index(mode) - 1] if mode else ""
                companies = card.find_all("a", href=re.compile(r"^/companies/"))  # its logo, then its name
                company = next((a.get_text(" ", strip=True) for a in companies if a.get_text(strip=True)), "")
                title = words[0]  # the job link is an empty layer over the whole card
                if role_of(title):
                    rows.append(row("relomote", "Remote" if mode == "Remote" else None, title, company,
                                    f"{where} ({mode})" if mode else where, f"https://relomote.com{link['href']}",
                                    posted, None, {"card": card.get_text(" | ", strip=True)}))
            if past_window or not cards:
                break
    return rows


def freehire() -> list[dict]:
    """freehire.me's public job API (no key; its robots.txt lets all but Googlebot use /api/): each
    keyword, posted in the last day, remote anywhere and any job in Egypt, the 100 most relevant of
    each with the full description. Only jobs it rates fresh: not old ones posted again, and not
    falsely refreshed. 1 second apart."""
    rows = []
    for keyword in KEYWORDS:
        for where in ({"work_mode": "remote"}, {"countries": "eg"}):
            if time_left(EXTRACT_SECONDS) < 15:
                print("freehire: time budget reached, the rest waits for the next run")
                return rows
            time.sleep(1)
            try:
                jobs = get("https://freehire.me/api/v1/agent/jobs/search", q=keyword, limit=100, posted_within_days=1,
                           semantic_ratio=0, include_description="true", description_format="text",
                           **where).json().get("data", [])
            except Held as e:  # keep what the searches before it found
                print(f"freehire: {e}")
                return rows
            for j in jobs:
                reality, mode = j.get("reality") or {}, j.get("work_mode") or ""
                place_text = "Worldwide" if "global" in (j.get("regions") or []) else j.get("location") or ""
                location = f"{place_text} ({mode})" if mode else place_text
                if (reality.get("class") == "fresh" and not reality.get("fake_freshness") and j.get("url")
                        and role_of(j.get("title") or "") and place_of(location)):
                    rows.append(row("freehire", "Remote" if mode == "remote" else None, j["title"], j.get("company"),
                                    location, j["url"], (j.get("posted_at") or "")[:10] or None, j.get("description"),
                                    {k: v for k, v in j.items() if k != "description"}))
    return rows


def jooble() -> list[dict]:
    """Jooble's official API, which gathers the local boards it indexes; needs a free key in
    JOOBLE_API_KEY (https://jooble.org/api/about), skipped without one."""
    key = os.environ.get("JOOBLE_API_KEY")
    if not key:
        print("jooble: no JOOBLE_API_KEY, skipped")
        return []

    def search(args: tuple) -> list[dict]:
        keyword, (place, location, _) = args
        if time_left(EXTRACT_SECONDS) < 15:  # Indeed and Bayt came first in the same step
            return []
        r = fetch("POST", f"https://jooble.org/api/{key}", json={"keywords": keyword, "location": location})
        return [row("jooble", place, j["title"], j.get("company"), j.get("location"), j["link"],
                    (j.get("updated") or "")[:10], j.get("snippet"), j)
                for j in r.json().get("jobs", []) if (j.get("updated") or "")[:10] >= since() and role_of(j["title"])]

    with ThreadPoolExecutor(4) as pool:
        return [r for rows in pool.map(search, [(k, p) for k in KEYWORDS for p in PLACES]) for r in rows]


def workable_jobs() -> list[dict]:
    """Workable's public job search, across every company on Workable (startups and small and mid
    companies above all): each keyword in each place (remote jobs only outside Egypt), posted in the
    last day, 4 searches at a time. Workable allows few searches a day (after 3 runs of these 234
    in a morning it answered 429 with a 21-hour Retry-After), and each search covers the whole last
    day: so one round a day, held for 20 hours after it (one of the daily collects runs it), and
    a 429 holds it for as long as Workable asks."""
    host = "jobs.workable.com"
    if left := waiting(host):
        print(f"workable: held for {duration(left)} more")
        return []

    def search(args: tuple) -> list[dict] | None:
        keyword, (place, location, _) = args
        try:
            jobs = get(f"https://{host}/api/v1/jobs", query=keyword, location=location, day_range=1,
                       workplace=None if place in ONSITE_PLACES else "remote").json()["jobs"]
        except Held:
            return None  # its 429 or 403: this search and the ones after it wait for the hold
        except ERRORS as e:  # one search down is a short day
            print(f"WARNING workable {keyword} / {location}: {e!r}"[:200])
            return []
        rows = []
        for j in jobs:
            where = j.get("location") or {}
            location_text = ", ".join(filter(None, [where.get("city"), where.get("countryName")]))
            text = BeautifulSoup(f"{j.get('description', '')} {j.get('requirementsSection', '')}", "html.parser")
            if role_of(j["title"]) and place_of(location_text):
                rows.append(row("workable", "Remote" if j.get("workplace") == "remote" else None, j["title"],
                                (j.get("company") or {}).get("title"), location_text, j["url"], j["created"][:10],
                                text.get_text(" ", strip=True), j))
        return rows

    with ThreadPoolExecutor(4) as pool:
        found = list(pool.map(search, [(k, p) for k in KEYWORDS for p in PLACES]))
    if None in found:  # the hold is Workable's own Retry-After (or 6 hours after a 403): never shortened by ours
        print(f"workable: refused after {sum(rows is not None for rows in found)} of {len(found)} searches, "
              f"held for {duration(waiting(host))}")
    else:
        hold(host, 20 * 3600)
    return [r for rows in found if rows for r in rows]


def company_portals() -> list[dict]:
    """28,000+ companies' own career pages (Greenhouse, Lever, Ashby, Workday, BambooHR, iCIMS,
    Paylocity) from job-board-aggregator's daily crawl (by Riley Dorrington, data CC BY-NC 4.0):
    one ~75 MB download, 8 chunks at a time, instead of 28,000 calls, and only when the feed has
    changed since it was last read (its manifest's last_updated). Kept: first seen in the window, a
    role, a place in scope."""
    def chunk(name: str) -> list[dict]:
        return json.loads(gzip.decompress(get(f"{PORTAL_FEED}/{name}").content))

    manifest = get(f"{PORTAL_FEED}/jobs_manifest.json").json()
    updated = str(manifest.get("last_updated") or "")
    if updated and FEED_READ.exists() and FEED_READ.read_text() == updated:
        print(f"company_portals: the feed is unchanged since {updated}, nothing to download")
        return []
    rows = []
    with ThreadPoolExecutor(8) as pool:
        for jobs in pool.map(chunk, manifest["chunks"]):
            for j in jobs:
                if ((j.get("first_seen") or "")[:10] >= since() and j.get("title") and j.get("url")
                        and role_of(j["title"]) and place_of(j.get("location") or "")):
                    rows.append(row(j.get("ats", "portal").lower(), None, j["title"], j.get("company"),
                                    j["location"], j["url"], j["first_seen"][:10], None, j))
    # marked read before the rows are saved: if saving fails, these jobs come with the feed's next update
    FEED_READ.parent.mkdir(parents=True, exist_ok=True)
    FEED_READ.write_text(updated)
    return rows


def wuzzuf() -> list[dict]:
    """Wuzzuf, through the JSON API its own web app calls (robots.txt allows it; only its search
    page sits behind Cloudflare's challenge, and this does not touch it): every job in Egypt,
    newest first, 50 a page with their details 20 per call, until a page has no job from the last
    HOURS_OLD hours (its own "last 24 hours" filter stops at 50 jobs, too few on a busy day). The
    window is counted from Wuzzuf's newest job, so its time zone does not matter; 1 second between
    calls. The title rules keep yours."""
    api = {**BROWSER, "Content-Type": "application/vnd.api+json", "Accept": "application/vnd.api+json"}
    rows, cutoff = [], None
    for start in range(0, 2000, 50):
        if time_left(EXTRACT_SECONDS) < 60:  # Tanqeeb comes after it in the same step
            print("wuzzuf: time budget reached, the rest waits for the next run")
            break
        body = {"startIndex": start, "pageSize": 50, "longitude": "0", "latitude": "0", "query": "", "searchFilters": {}}
        try:
            hits = fetch("POST", "https://wuzzuf.net/api/search/job", data=json.dumps(body), headers=api,
                         timeout=30).json().get("data", [])
            companies = {hit["id"]: ({f["name"]: f["value"] for f in hit["attributes"].get("computedFields", [])}
                                     .get("company_name") or [""])[0].strip() for hit in hits}
            ids, jobs = list(companies), []
            for i in range(0, len(ids), 20):
                time.sleep(1)
                jobs += fetch("GET", "https://wuzzuf.net/api/job", params={"filter[other][ids]": ",".join(ids[i:i + 20])},
                              headers=api, timeout=30).json().get("data", [])
        except Held as e:  # keep what the pages before it found
            print(f"wuzzuf: {e}")
            break
        posted_at = {job["id"]: datetime.strptime(job["attributes"]["postedAt"], "%m/%d/%Y %H:%M:%S")
                     for job in jobs if job["attributes"].get("postedAt")}
        if not posted_at:
            break
        cutoff = cutoff or max(posted_at.values()) - timedelta(hours=HOURS_OLD)
        recent = [job for job in jobs if posted_at.get(job["id"], cutoff) > cutoff]
        for job in recent:
            a, where = job["attributes"], job["attributes"].get("location") or {}
            location = ", ".join(filter(None, [(where.get("city") or {}).get("name"), (where.get("country") or {}).get("name")]))
            if role_of(a["title"]) and place_of(location):
                text = BeautifulSoup(f"{a.get('description') or ''} {a.get('requirements') or ''}", "html.parser")
                rows.append(row("wuzzuf", None, a["title"], companies[job["id"]], location, f"https://wuzzuf.net/{a['uri']}",
                                str(posted_at[job["id"]].date()), text.get_text(" ", strip=True), job))
        if not recent:  # newest first: a page with nothing from the window ends it
            break
        time.sleep(1)
    return rows


def tanqeeb() -> list[dict]:
    """Tanqeeb, which gathers Wuzzuf, Bayt, Forasna, NaukriGulf, GulfTalent ... for Egypt and the
    Gulf: its job pages by field (TANQEEB_PAGES), newest first, each card's title,
    company, place, date and the board it came from; its country sites side by side."""
    def text(card, css: str) -> str:
        element = card.select_one(css)
        return element.get_text(" ", strip=True) if element else ""

    def read_site(site: str, place: str) -> list[dict]:
        rows = []
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

    with ThreadPoolExecutor(len(TANQEEB_SITES)) as pool:
        return [r for rows in pool.map(read_site, TANQEEB_SITES, TANQEEB_SITES.values()) for r in rows]


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


def naukrigulf() -> list[dict]:
    """NaukriGulf (the Gulf), through the search API its web app calls; it drops plain requests, so
    request() asks it as a real Chrome. The 30 newest jobs of each keyword, from the last week (its
    dates run days late). It has no remote filter: a job whose title or summary says remote is marked
    so, and outside Egypt only those are kept (your rule)."""
    api = {"appid": "205", "systemid": "2323", "accept": "application/json"}
    rows, urls = [], set()
    for keyword in KEYWORDS:
        if time_left(EXTRACT_SECONDS) < 15:
            print("naukrigulf: time budget reached, the rest waits for the next run")
            break
        try:
            jobs = fetch("GET", "https://www.naukrigulf.com/spapi/jobapi/search", headers=api, timeout=30, params={
                "Keywords": keyword, "Limit": 30, "Offset": 0, "SortPreference": "date", "pageNo": 1}).json()["Jobs"]
        except Held as e:  # keep what the searches before it found
            print(f"naukrigulf: {e}")
            break
        for job in (j["Job"] for j in jobs):
            title, info, url = job.get("Designation") or "", job.get("jobInfo") or "", job.get("JdURL")
            posted = str(datetime.fromtimestamp(int(job["LatestPostedDate"]), timezone.utc).date())
            if posted >= week() and url and url not in urls and role_of(title) and place_of(job.get("Location") or ""):
                urls.add(url)
                remote = re.search(REMOTE, f"{title} {info}", re.I) is not None
                rows.append(row("naukrigulf", None, title, (job.get("Company") or {}).get("Name", "").strip(),
                                job["Location"], url, posted, info, {**job, "is_remote": remote}))
    return rows


def gulftalent() -> list[dict]:
    """GulfTalent (the Gulf), through the search API its pages call; it turns plain requests away, so
    request() asks it as a real Chrome. The 25 newest jobs of each keyword (it matches the whole text,
    so the title rules pick yours), from the last week; its remote filter returns nothing, so its
    is_remote mark decides, and outside Egypt only remote jobs are kept (your rule)."""
    rows, urls = [], set()
    for keyword in KEYWORDS:
        if time_left(EXTRACT_SECONDS) < 15:
            print("gulftalent: time budget reached, the rest waits for the next run")
            break
        try:
            jobs = get("https://www.gulftalent.com/api/jobs/search", version=2, search_keyword=keyword, search_order="d",
                       limit=25, offset=0, include_scraped=1).json()["results"]["data"]
        except Held as e:  # keep what the searches before it found
            print(f"gulftalent: {e}")
            break
        for j in jobs:
            url, posted = f"https://www.gulftalent.com{j['link']}", str(date.fromtimestamp(int(j["posted_date_ts"])))
            company = j.get("jb_company_name") if str(j.get("is_job_board")) == "1" else j.get("company_name")
            if posted >= week() and url not in urls and role_of(j["title"]) and place_of(j.get("location") or ""):
                urls.add(url)
                rows.append(row("gulftalent", None, j["title"], company, j["location"], url, posted, None,
                                {**j, "is_remote": str(j.get("is_remote")) == "1"}))
    return rows


def dubizzle() -> list[dict]:
    """Dubizzle Jobs (UAE), through the public search index (Algolia) its pages query - the pages
    themselves sit behind a bot check: its remote jobs of the last week, newest first, in one call."""
    hits = fetch("POST", "https://WD0PTZ13ZS-dsn.algolia.net/1/indexes/by_added_desc_jobs.com/query",
                 headers={"x-algolia-application-id": "WD0PTZ13ZS",
                          "x-algolia-api-key": "cdd839b4fdac840289e88633779e8634"},
                 json={"hitsPerPage": 100, "filters": f'added > {int(time.time()) - 7 * 86400}'
                                                      ' AND "details.Remote Job.en.value":"Yes"'}).json()["hits"]
    rows = []
    for h in hits:
        title = (h.get("name") or {}).get("en") or ""
        location = ", ".join(reversed((h.get("location_list") or {}).get("en") or ["UAE"])) + " (remote)"
        if role_of(title) and place_of(location):
            company = (((h.get("details") or {}).get("Company Name") or {}).get("en") or {}).get("value")
            rows.append(row("dubizzle", None, title, company, location, h["absolute_url"]["en"],
                            str(date.fromtimestamp(int(h["created_at"]))), None, h))
    return rows


def company_sites(sites: list[tuple]) -> tuple[list[dict], list[tuple]]:
    """The career sites in core.company, side by side: (company, careers_url, platform, api) each.
    A site not yet known is first detected (platform, api); the rows come with each site's
    (platform, api, note, company), for core.company."""
    def read(site: tuple) -> tuple[list[dict], tuple]:
        company, url, platform, api = site
        note = None
        if platform in (None, "blocked", "forbidden", "unreachable"):
            platform, api, note = detect(url)
        if platform in (None, "blocked", "forbidden", "unreachable", "covered"):
            print(f"{company}: {platform or 'not reached, tried again next run'} ({note})")
            return [], (platform, api, note, company)
        try:
            rows = READERS[platform](company, api)
        except Exception as e:  # one site down is a short day; a refusal is detected again next run,
            # so a site whose saved session expired shows as blocked, for scripts/open_blocked.py
            print(f"WARNING {company}: {e!r}"[:300])
            refused = isinstance(e, (Held, Challenge))
            return [], (None if refused else platform, api, f"read failed: {e!r}"[:200], company)
        print(f"{company} ({platform}): {len(rows)}")
        return rows, (platform, api, note, company)

    # Each may start a browser: more starves the task's heartbeat. A batch ends 60 seconds before the
    # step's time budget: a site still loading (a page can take a minute) finishes in that time,
    # since the step waits for it before it exits. A site not done by then is left for the next run.
    pool = ThreadPoolExecutor(4)
    done, _ = wait([pool.submit(read, site) for site in sites], timeout=max(0, time_left(EXTRACT_SECONDS) - 60))
    pool.shutdown(wait=False, cancel_futures=True)
    results = [f.result() for f in done]
    return [r for rows, _ in results for r in rows], [found for _, found in results]


# boards another source already reads in bulk, so no browser is spent on them every run
COVERED = [(r"myworkdayjobs\.com|bamboohr\.com", "the 28,000-company feed (extract_portals)"),
           (r"jobs\.workable\.com/company/", "the Workable job search (extract_workable)")]
# a careers page -> its platform: the platform's address in the page's URL or code
ATS_PATTERNS = [
    ("workable", r"(?:apply\.workable\.com/|workable\.com/api/v1/widget/accounts/)(?!api\b)([\w-]+)"),
    ("greenhouse", r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)"),
    ("lever", r"jobs\.lever\.co/([\w-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([\w.-]+)"),
]


def detect(url: str) -> tuple[str, str | None, str | None]:
    """(platform, what its reader calls, note) for a careers page: 'blocked' when it shows a bot
    check even to a real Chrome's handshake and a browser (you open it once: scripts/open_blocked.py),
    'page' when no known platform shows, even once Playwright has run the page's JavaScript (its
    links are read); None when the network failed, so the next run tries again."""
    parts = urlsplit(url)
    for platform, pattern in ATS_PATTERNS:  # the URL itself names the platform: nothing to fetch
        if m := re.search(pattern, url):
            return platform, m.group(1), None
    for pattern, reader in COVERED:
        if re.search(pattern, url):
            return "covered", None, f"its jobs come through {reader}"
    if left := waiting(parts.hostname):  # the site asked us to wait: not even a detection before then
        return None, None, f"held for {duration(left)} more"
    try:
        r = request("GET", url, timeout=30)
    except ERRORS as e:
        return None, None, f"network error: {e!r}"[:200]
    page = r.text
    if r.status_code == 429:
        hold(parts.hostname, retry_after(r.headers.get("Retry-After")))
        return None, None, f"it asked to wait (HTTP 429): held for {duration(waiting(parts.hostname))}"
    if r.status_code in (401, 403) or re.search(CHALLENGE, page[:20000]):
        return "blocked", None, f"it shows a bot check (HTTP {r.status_code}): run scripts/open_blocked.py"
    if r.status_code >= 400:
        return "unreachable", None, f"page not found (HTTP {r.status_code}): check the careers URL"
    for platform, pattern in ATS_PATTERNS:
        if m := re.search(pattern, page):
            return platform, m.group(1), None
    if re.search(r"phenompeople|phApp\.", page):
        return "phenom", re.split(r"/search-results|/job/", url)[0].rstrip("/"), None
    if re.search(r"jobTitle-link|rmkcdn|successfactors", page):
        return "successfactors", f"{parts.scheme}://{parts.netloc}", None
    if page.lstrip()[:200].startswith(("<?xml", "<rss")):
        return "rss", url, None
    try:  # a job board its JavaScript loads (an embedded Greenhouse or Workable list ...)
        page = rendered(url)
    except Challenge as e:
        return "blocked", None, f"{e}"[:160] + ": run scripts/open_blocked.py"
    except Exception as e:  # a slow page, or the browser itself: read again next run
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
    are one of the roles, in a place its title or the text around it names (a job naming no place
    is left out: many of your companies hire abroad too)."""
    soup = BeautifulSoup(rendered(url), "html.parser")
    rows, urls = [], set()
    for a in soup.find_all("a", href=True):
        title, job_url = a.get_text(" ", strip=True), urljoin(url, a["href"])
        context = a.parent.get_text(" ", strip=True) if a.parent else ""
        if role_of(title) and place_of(context) and job_url not in urls:
            urls.add(job_url)
            rows.append(row(company.lower(), None, title, company, context[:200], job_url,
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
    """A SuccessFactors career site (Nestlé): its newest jobs in Egypt (no remote filter), one page
    per place."""
    rows = []
    for _, location, _ in [p for p in PLACES if p[0] in ONSITE_PLACES]:
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
    pages that build their job list in the browser. Each worker thread starts one Chromium and
    reuses it for every page (Playwright is one instance per thread); it ends with the step. The
    page is read once it has loaded and gone quiet, or 10 seconds after it loaded: busy sites (IBM,
    Bain, Capgemini) never go quiet, and waiting for it timed them out. A site you opened by hand
    gets your session; a page that still shows a bot check raises, for you to open."""
    from playwright.sync_api import Error as PageError, TimeoutError as PageTimeout, sync_playwright
    if not hasattr(BROWSERS, "chromium"):
        BROWSERS.chromium = sync_playwright().start().chromium.launch()
    saved = session(urlsplit(url).hostname)
    context = BROWSERS.chromium.new_context(
        user_agent=saved["user_agent"] if saved else BROWSER["User-Agent"],
        storage_state={"cookies": saved["cookies"], "origins": []} if saved else None)
    try:
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except PageError as e:
            if "ERR_HTTP2_PROTOCOL_ERROR" in str(e):  # how McKinsey's site (Akamai) drops headless Chromium
                raise Challenge("it drops headless Chromium (ERR_HTTP2_PROTOCOL_ERROR)") from e
            raise
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except PageTimeout:  # still busy: what has loaded is read
            pass
        html = page.content()
    finally:
        context.close()
    if re.search(CHALLENGE, html[:20000]):
        raise Challenge("its page shows a bot check")
    return html


READERS = {"workable": workable, "greenhouse": greenhouse, "lever": lever, "ashby": ashby, "phenom": phenom,
           "successfactors": successfactors, "rss": rss, "page": career_page}


def mailboxes(seen: set[str]) -> list[dict]:
    """Jobs in every email of your inboxes and spam folders (LinkedIn, Indeed, Wuzzuf, Wellfound,
    Bayt alerts, an employer's email ...), over IMAP and read-only: nothing is marked read, moved or
    deleted, and nothing leaves the laptop. An email already read for jobs (its Message-ID in
    `seen`) is not read again.

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
        # the inbox, then the spam folder: the one the server flags \Junk (Gmail's [Gmail]/Spam)
        spam = [re.search(r'"?([^"]*)"?$', line.decode()).group(1) for line in imap.list()[1] if b"\\Junk" in line]
        since_day = (date.today() - timedelta(hours=HOURS_OLD)).strftime("%d-%b-%Y")
        for folder in ["INBOX", *spam]:
            imap.select(f'"{folder}"', readonly=True)
            for uid in imap.uid("search", None, "SINCE", since_day)[1][0].split():
                info, head = imap.uid("fetch", uid, "(RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT MESSAGE-ID DATE)])")[1][0]
                h = email.message_from_bytes(head, policy=email.policy.default)
                message_id = str(h["Message-ID"] or f"{address}/{folder}/{uid.decode()}")
                size = int(re.search(rb"RFC822\.SIZE (\d+)", info).group(1))
                if message_id in seen or str(h["Subject"]).startswith("Job radar") or size > MAIL_BYTES:
                    continue  # read before, its own digest, or attachments
                msg = email.message_from_bytes(imap.uid("fetch", uid, "(BODY.PEEK[])")[1][0][1],
                                               policy=email.policy.default)
                body = msg.get_body(preferencelist=("html",))
                alert = re.search(LINKEDIN_ALERTS, str(h["From"]), re.I) is not None
                jobs = jobs_in_email(body.get_content(), alert) if body else []
                if not re.search(JOB_SENDERS, str(h["From"]), re.I):
                    jobs = [job for job in jobs if re.search(JOB_PAGE, job[3], re.I)]
                    if not jobs:  # not a job email
                        continue
                posted = str(parsedate_to_datetime(str(h["Date"])).date()) if h["Date"] else None
                meta = {"mailbox": address, "folder": folder, "message_id": message_id, "from": str(h["From"]),
                        "subject": str(h["Subject"])}
                for title, company, location, url in jobs:
                    # the place comes from the location or the title in transform, else "Unknown location"
                    rows.append(row("email", None, title, company, location, url, posted,
                                    None, {**meta, "title": title, "company": company, "location": location, "url": url}))
                print(f"email {address} {folder}: {h['Subject']!s:.60} -> {len(jobs)} jobs")
    return rows


def jobs_in_email(page: str, alert: bool = False) -> list[tuple[str, str, str, str]]:
    """(title, company, location, url) of each link in a job email whose words are one of the roles;
    in a LinkedIn alert (alert), each of its job links whatever the title.
    Job alerts put the company and location on the line under the title ("Company · Location");
    a LinkedIn alert puts the whole card in one link: title, "Company · Location", then extras; a
    Wellfound alert puts the card above a "Learn more" link: title, then company."""
    soup = BeautifulSoup(page, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        links.append((a.get_text("\n", strip=True), a["href"]))
        a.replace_with(f"\n@@{len(links) - 1}@@\n")
    lines = [line.strip() for line in soup.get_text("\n").splitlines() if line.strip()]
    jobs, urls, after_link = [], set(), 0
    for i, line in enumerate(lines):
        link = re.fullmatch(r"@@(\d+)@@", line)
        if not link:
            continue
        above, after_link = lines[after_link:i], i + 1  # the text since the previous link
        text, url = links[int(link.group(1))]
        title, _, card = text.partition("\n")
        if re.fullmatch(GENERIC_LINK, title, re.I):  # the card is the text above: its first role line, then the rest
            first = next((k for k, text_line in enumerate(above) if role_of(text_line)), None)
            if first is None:
                continue
            title, card = above[first], "\n".join(above[first + 1:])
        url = canonical_url(url)
        # from LinkedIn only its job pages (not profiles, posts or searches); in its alert, each kept
        # whatever its title: LinkedIn chose it for your alert
        linkedin_job = "linkedin.com/jobs/view/" in url
        if not role_of(title, alert=alert and linkedin_job) or url in urls or ("linkedin.com" in url and not linkedin_job):
            continue
        urls.add(url)
        after = card.split("\n")[0] if card else lines[i + 1] if i + 1 < len(lines) else ""
        if (next_link := re.fullmatch(r"@@(\d+)@@", after)):  # the company is a link of its own
            after = links[int(next_link.group(1))][0].split("\n")[0]
        company, location = (part.strip(" -") for part in (re.split(r"\s+[·•|–-]\s+", after, maxsplit=1) + [""])[:2])
        if not location and not card and i + 2 < len(lines) and not lines[i + 2].startswith("@@"):
            location = lines[i + 2]  # the location on a line of its own
        jobs.append((title, company if not role_of(company) else "", location, url))
    return jobs


def canonical_url(url: str) -> str:
    """One link per job: a LinkedIn alert's tracking link as the plain job page, a tracking link
    that carries the job page's address in its path (Wuzzuf's) as that page, and any link without
    its utm_ tracking parameters (which differ from one email to the next)."""
    m = re.search(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)", url)
    if m:
        return f"https://www.linkedin.com/jobs/view/{m.group(1)}"
    if inner := re.search(r"/(https?(?::|%3A)%2F%2F[^/&]+)", url, re.I):
        return canonical_url(unquote(inner.group(1)))
    parts = urlsplit(url)
    return urlunsplit(parts._replace(query=urlencode(
        [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith("utm_")])))
