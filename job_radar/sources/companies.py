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

from ..config import EXTRACT_SECONDS, HOME, ONSITE_PLACES, PHENOM_SEARCHES, PLACES, OUTPUT, place_of, role_of
from .base import (CHALLENGE, SITE, Challenge, ERRORS, Held, duration, fetch, get, hold, rendered, request, retry_after, row,
                   since, time_left, waiting)


PORTAL_FEED = "https://riley-dorrington.github.io/job-board-data/data/chunks"

# the feed's last_updated and the chunks of that version read so far, as JSON: a feed is downloaded again only once
# it changes, and a version only part read (the time budget) is continued by the next run
FEED_READ = OUTPUT / "feed-last-updated.txt"
CHUNK_THREADS = 4  # a chunk is ~10 MB of gzip and ~25,000 jobs once read; 4 at a time peaked ~290 MiB over idle, the 8 before it ran when Postgres crashed (2026-10-11)
SITE_SECONDS = 60  # one career site's whole read, its detection included (company_sites)
CHUNK_SECONDS = 40  # one chunk's download; no chunk is started with less than this left of the step's time budget


def company_portals() -> list[dict]:
    """28,000+ companies' own career pages (Greenhouse, Lever, Ashby, Workday, BambooHR, iCIMS,
    Paylocity) from job-board-aggregator's daily crawl (by Riley Dorrington, data CC BY-NC 4.0):
    one ~75 MB download in 8 chunks, 4 at a time, instead of 28,000 calls, and only when the feed has
    changed since it was last read (its manifest's last_updated). A run that runs out of time keeps the
    chunks it read and the next run reads the rest. Kept: first seen in the window, a role, a place in scope."""
    def chunk(name: str) -> list[dict] | None:
        """One chunk's jobs in scope (None: too little time was left to start it). Filtered here, so its ~25,000
        jobs are freed at once instead of waiting in memory until the chunks before it are read."""
        if time_left(EXTRACT_SECONDS) < CHUNK_SECONDS:
            return None
        data = gzip.decompress(fetch("GET", f"{PORTAL_FEED}/{name}", timeout=CHUNK_SECONDS).content)
        return [row(j.get("ats", "portal").lower(), None, j["title"], j.get("company"), j["location"], j["url"],
                    j["first_seen"][:10], None, j)
                for j in json.loads(data)
                if ((j.get("first_seen") or "")[:10] >= since() and j.get("title") and j.get("url")
                    and role_of(j["title"]) and place_of(j.get("location") or ""))]

    manifest = get(f"{PORTAL_FEED}/jobs_manifest.json").json()
    version, chunks = str(manifest.get("last_updated") or ""), manifest["chunks"]
    read = set()
    try:
        saved = json.loads(FEED_READ.read_text())
        if version and saved["version"] == version:
            read = set(saved["chunks"])
    except (OSError, ValueError, TypeError, KeyError):  # no file, or the old one holding a bare time: nothing read
        pass
    todo = [name for name in chunks if name not in read]
    if not todo and version:
        print(f"company_portals: the feed is unchanged since {version}, all {len(chunks)} chunks read, nothing to download")
        return []
    # not `with`: its exit waits for every chunk, and a chunk still downloading when the budget ends is left behind
    pool = ThreadPoolExecutor(CHUNK_THREADS)
    futures = {pool.submit(chunk, name): name for name in todo}
    done, _ = wait(futures, timeout=max(0, time_left(EXTRACT_SECONDS)))
    pool.shutdown(wait=False, cancel_futures=True)
    found = {}
    for future in (f for f in futures if f in done):  # the feed's order
        if future.exception():
            print(f"WARNING company_portals: {futures[future]}: {future.exception()!r}"[:300])
        elif future.result() is not None:
            found[futures[future]] = future.result()
    read |= found.keys()
    # marked read before the rows are saved: if saving fails, these jobs come with the feed's next update
    FEED_READ.parent.mkdir(parents=True, exist_ok=True)
    FEED_READ.write_text(json.dumps({"version": version, "chunks": sorted(read)}))
    print(f"company_portals: read {len(read)} of {len(chunks)} chunks (feed {version})")
    return [r for rows in found.values() for r in rows]


def company_sites(sites: list[tuple]) -> tuple[list[dict], list[tuple]]:
    """The career sites in core.company, side by side: (company, careers_url, platform, api) each,
    keeping only their jobs in Egypt. A site not yet known is first detected (platform, api); the rows
    come with each site's (platform, api, note, company), for core.company."""
    def read(site: tuple) -> tuple[list[dict], tuple]:
        company, url, platform, api = site
        SITE.until = time.monotonic() + SITE_SECONDS  # every request and page wait of this site ends by then
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

    # Each may start a browser: more starves the task's heartbeat. A batch ends SITE_SECONDS before the
    # step's time budget: a site still loading finishes in that time (read() gives every site SITE_SECONDS at
    # most). __main__ leaves without waiting for a site not done by then. One still loading is stamped with a note,
    # so the next run moves on to the others: unstamped, the same slow sites came first every run and the batch
    # gave up on them every time (40 sites never read, 2026-10-11). One never started is left first in line.
    pool = ThreadPoolExecutor(4)
    futures = {pool.submit(read, site): site for site in sites}
    done, _ = wait(futures, timeout=max(0, time_left(EXTRACT_SECONDS) - SITE_SECONDS))
    pool.shutdown(wait=False, cancel_futures=True)
    results = [f.result() for f in done]
    late = [(platform, api, "not read: the run's time ran out while it loaded", company)
            for f, (company, _, platform, api) in futures.items() if not f.done()]
    return [r for rows, _ in results for r in rows], [found for _, found in results] + late


# boards another source already reads in bulk, so no browser is spent on them every run
COVERED = [(r"myworkdayjobs\.com|bamboohr\.com", "the 28,000-company feed (extract_portals)"),
           (r"jobs\.workable\.com/company/", "the Workable job search (extract_workable)")]


# a careers page -> its platform: the platform's address in the page's URL or code
ATS_PATTERNS = [
    ("workable", r"(?:apply\.workable\.com/|workable\.com/api/v1/widget/accounts/)(?!api\b)([\w-]+)"),
    ("greenhouse", r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)"),
    ("lever", r"jobs\.lever\.co/([\w-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([\w.-]+)"),
    ("successfactors_classic", r"(https://career\d*\.successfactors\.\w+/career\?company=[\w-]+)"),
    ("smartrecruiters", r"(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)"),
    ("recruitee", r"\b(?!www\.|app\.|api\.)([\w-]+)\.recruitee\.com"),
    ("personio", r"\b([\w-]+)\.jobs\.personio\.(?:de|com)"),
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


def smartrecruiters(company: str, slug: str) -> list[dict]:
    """A company's open jobs in Egypt on SmartRecruiters, through its public postings API (it filters by country, so a
    company with thousands of jobs elsewhere is a page or two), 100 a page. Its list has no description: describe reads
    the job page."""
    rows = []
    for offset in range(0, 1000, 100):
        page = get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings", country="eg", limit=100,
                   offset=offset).json()
        for j in page.get("content") or []:
            where = j.get("location") or {}
            location = (where.get("fullLocation") or ", ".join(filter(None, [where.get("city"), where.get("country")]))) \
                + (" (remote)" if where.get("remote") else " (hybrid)" if where.get("hybrid") else "")
            if role_of(j.get("name") or "") and place_of(location):
                rows.append(row(company.lower(), None, j["name"], company, location,
                                f"https://jobs.smartrecruiters.com/{slug}/{j['id']}", (j.get("releasedDate") or "")[:10] or None,
                                None, j))
        if offset + 100 >= (page.get("totalFound") or 0):
            break
    return rows


def recruitee(company: str, slug: str) -> list[dict]:
    """Every open job of a company on Recruitee, through its public offers API."""
    rows = []
    for j in get(f"https://{slug}.recruitee.com/api/offers/").json().get("offers") or []:
        location = (j.get("location") or ", ".join(filter(None, [j.get("city"), j.get("country")]))) \
            + (" (remote)" if j.get("remote") else " (hybrid)" if j.get("hybrid") else "")
        if j.get("careers_url") and role_of(j.get("title") or "") and place_of(location):
            text = BeautifulSoup(j.get("description") or "", "html.parser").get_text(" ", strip=True) or None
            rows.append(row(company.lower(), None, j["title"], company, location, j.get("careers_url"),
                            (j.get("published_at") or "")[:10] or None, text, j))
    return rows


def personio(company: str, slug: str) -> list[dict]:
    """Every open job of a company on Personio, through its public XML feed (its main office and the others it names)."""
    rows = []
    for position in ET.fromstring(get(f"https://{slug}.jobs.personio.de/xml").content).iter("position"):
        title = position.findtext("name", "").strip()
        offices = [o.text or "" for o in position.iter("office")]  # its main office, then the others
        location = ", ".join(dict.fromkeys(o.strip() for o in offices if o and o.strip()))
        text = " ".join(BeautifulSoup(d.findtext("value", ""), "html.parser").get_text(" ", strip=True)
                        for d in position.iter("jobDescription")) or None
        if role_of(title) and place_of(location):
            rows.append(row(company.lower(), None, title, company, location,
                            f"https://{slug}.jobs.personio.de/job/{position.findtext('id')}",
                            position.findtext("createdAt", "")[:10] or None, text,
                            {child.tag: child.text for child in position if len(child) == 0}))
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
            # the place its text names, not the text: Capgemini's link text is title, city and team in one line
            rows.append(row(company.lower(), None, title, company, place_of(context), job_url,
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


def successfactors_classic(company: str, url: str) -> list[dict]:
    """A classic SuccessFactors career portal (EG Bank, career?company=...): its jobs show only once Search is pressed,
    newest first; the first page (10 jobs) is read. It names no place, so its jobs are the company's own, in Egypt."""
    soup = BeautifulSoup(rendered(url, click='input[type=submit][value*="Search"], button:has-text("Search")'), "html.parser")
    portal = url.split("?")[0]
    rows = []
    for a in soup.select("a.jobTitle"):
        title, req = a.get_text(" ", strip=True), re.search(r"career_job_req_id=(\d+)", a["href"])
        note = a.find_parent("td").get_text(" ", strip=True) if a.find_parent("td") else ""
        when = re.search(r"Posted on (\d{2}/\d{2}/\d{4})", note)
        posted = str(datetime.strptime(when.group(1), "%d/%m/%Y").date()) if when else ""
        if req and posted >= since() and role_of(title):
            job_url = f"{portal}?career_ns=job_listing&{urlsplit(url).query}&career_job_req_id={req.group(1)}"
            rows.append(row(company.lower(), None, title, company, HOME, job_url, posted, None,
                            {"title": title, "requisition": req.group(1), "date": posted}))
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
           "smartrecruiters": smartrecruiters, "recruitee": recruitee, "personio": personio,
           "successfactors": successfactors, "successfactors_classic": successfactors_classic, "rss": rss, "page": career_page}
