"""Your companies: each one's own career site (its Egypt jobs), and the 28,000-company feed."""

from __future__ import annotations

import gzip
import json
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ..config import EXTRACT_SECONDS, HOME, ONSITE_PLACES, PHENOM_SEARCHES, PLACES, ROOT, place_of, role_of
from .base import CHALLENGE, Challenge, ERRORS, Held, duration, get, hold, rendered, request, retry_after, row, since, time_left, waiting


PORTAL_FEED = "https://feashliaa.github.io/job-board-data/data/chunks"

# the feed's last_updated when it was last read: it is downloaded again only once it changes
FEED_READ = ROOT / "output" / "feed-last-updated.txt"


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


def company_sites(sites: list[tuple]) -> tuple[list[dict], list[tuple]]:
    """The career sites in core.company, side by side: (company, careers_url, platform, api) each,
    keeping only their jobs in Egypt. A site not yet known is first detected (platform, api); the rows
    come with each site's (platform, api, note, company), for core.company."""
    def read(site: tuple) -> tuple[list[dict], tuple]:
        company, url, platform, api = site
        note = None
        if platform in (None, "blocked", "forbidden", "unreachable"):
            platform, api, note = detect(url)
        if platform in (None, "blocked", "forbidden", "unreachable", "covered"):
            print(f"{company}: {platform or 'not reached, tried again next run'} ({note})")
            return [], (platform, api, note, company)
        try:
            rows = [r for r in READERS[platform](company, api) if place_of(r["location"]) == HOME]  # Egypt only
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


READERS = {"workable": workable, "greenhouse": greenhouse, "lever": lever, "ashby": ashby, "phenom": phenom,
           "successfactors": successfactors, "rss": rss, "page": career_page}
