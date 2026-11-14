"""Your companies: each one's own career site (its Egypt jobs), and the 28,000-company feed."""

from __future__ import annotations

import ast
import gzip
import html
import json
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from ..config import COMPANY_SECONDS, HOME, ONSITE_PLACES, PHENOM_SEARCHES, PLACES, OUTPUT, place_of, role_of
from .base import (CHALLENGE, SITE, Challenge, ERRORS, Held, duration, fetch, get, hold, rendered, request, retry_after, relative_date,
                   row, since, time_left, waiting)


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
        if time_left(COMPANY_SECONDS) < CHUNK_SECONDS:
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
    done, _ = wait(futures, timeout=max(0, time_left(COMPANY_SECONDS)))
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


# platforms whose reader starts a browser (or whose platform is still to be found: detect() may start one): 4 at a time,
# as each Chromium is heavy; every other reader is plain HTTP, which steps.extract_companies reads first and wider
NEEDS_BROWSER = {None, "page", "blocked", "forbidden", "unreachable", "successfactors_classic"}


def needs_browser(platform: str | None, url: str) -> bool:
    """Whether reading this site may start a browser. A site not yet detected (or retried) whose address already names its
    platform (a Greenhouse or Lever board) is detected from the address alone, so it is as cheap as one already known."""
    if platform in (None, "blocked", "forbidden", "unreachable"):
        platform = next((p for p, pattern in ATS_PATTERNS if re.search(pattern, url or "")), platform)
    return platform in NEEDS_BROWSER


FAST_THREADS = 12  # plain-HTTP readers at once (each host is still asked one request a second, sources.base.pace)


def company_sites(sites: list[tuple], threads: int = 4) -> tuple[list[dict], list[tuple]]:
    """The career sites in core.company, side by side: (company, careers_url, platform, api[, remote]) each,
    keeping only their jobs in Egypt (and, for a site marked remote, the remote ones open to someone in Egypt). A site
    not yet known is first detected (platform, api); the rows come with each site's (platform, api, note, company), for
    core.company. A site that cannot be read is one line in the log and one note, never an error for the others."""
    def read(site: tuple) -> tuple[list[dict], tuple]:
        company, url, platform, api, *flags = site
        places = {HOME, "Remote"} if flags and flags[0] else {HOME}
        SITE.until = time.monotonic() + SITE_SECONDS  # every request and page wait of this site ends by then
        note = None
        if platform == "page":  # detect() never runs again for a page: one stored before its platform had a reader is read by it now
            platform, api = next(((p, m.group(1)) for p, pattern in ATS_PATTERNS if (m := re.search(pattern, url))), (platform, api))
        if platform in (None, "blocked", "forbidden", "unreachable"):
            try:
                platform, api, note = detect(url)
            except Exception as e:  # an address that cannot even be requested: this site only, tried again next run
                print(f"WARNING {company}: detection failed: {e!r}"[:300])
                return [], (None, api, f"detection failed: {e!r}"[:200], company)
        if platform in (None, "blocked", "forbidden", "unreachable", "covered"):
            print(f"{company}: {platform or 'not reached, tried again next run'} ({note})")
            return [], (platform, api, note, company)
        try:
            rows = [r for r in READERS[platform](company, api) if place_of(r["location"]) in places]  # Egypt (and remote)
        except Exception as e:  # one site down is a short day, a refusal is detected again next run,
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
    pool = ThreadPoolExecutor(threads)
    futures = {pool.submit(read, site): site for site in sites}
    done, _ = wait(futures, timeout=max(0, time_left(COMPANY_SECONDS) - SITE_SECONDS))
    pool.shutdown(wait=False, cancel_futures=True)
    results = []
    for f in (f for f in futures if f in done):  # the sites' order
        if f.exception():  # read() answers for every error it can foresee; this is one it could not
            company, _, platform, api, *_ = futures[f]
            print(f"WARNING {company}: {f.exception()!r}"[:300])
            results.append(([], (platform, api, f"read failed: {f.exception()!r}"[:200], company)))
        else:
            results.append(f.result())
    late = [(site[2], site[3], "not read: the run's time ran out while it loaded", site[0])
            for f, site in futures.items() if not f.done()]
    return [r for rows, _ in results for r in rows], [found for _, found in results] + late


# boards another source already reads in bulk, so no browser is spent on them every run
COVERED = [(r"bamboohr\.com", "the 28,000-company feed (extract_portals)"),
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
    ("teamtailor", r"\b(?!www\.|app\.|api\.|career\.)([\w-]+)\.teamtailor\.com"),
    ("pinpoint", r"\b(?!www\.|app\.)([\w-]+)\.pinpointhq\.com"),
    ("zoho", r"(https://[\w-]+\.zohorecruit\.com/jobs/\w+)"),
    ("taleo", r"(https://[\w-]+\.taleo\.net/careersection/[\w-]+)/(?:jobsearch|joblist)\.ftl"),
    ("workday", r"(https://[\w.-]+\.myworkdaysite\.com/recruiting/[\w-]+/[\w-]+)"),
    ("workday", r"(https://[\w-]+\.wd\d+\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?[\w-]+)"),
    ("oracle_hcm", r"(https://[\w.-]+/hcmUI/CandidateExperience/\w+/sites/[\w-]+/jobs[^\s\"'<>#]*)"),
    ("eightfold", r"(https://[\w.-]+/careers\?[^\s\"'<>#]*\bdomain=[\w.-]+[^\s\"'<>#]*)"),
    ("ibm", r"(https://www\.ibm\.com/careers/search[^\s\"'<>#]*)"),
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
        if "teamtailor.com/locations" in page:  # a Teamtailor site on its own domain (careers.<company>.com)
            return "teamtailor", parts.netloc, None
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
    SITE.page = (url, page)  # career_page reads this very page: rendering it twice used the site's whole minute
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


def teamtailor(company: str, slug: str) -> list[dict]:
    """Every open job of a company on Teamtailor, through its public RSS feed (jobs.rss): each job names its cities and
    countries, and whether it is done remotely (fully) or hybrid."""
    locations = "{https://teamtailor.com/locations}"
    rows = []
    for item in ET.fromstring(get(f"https://{slug if '.' in slug else slug + '.teamtailor.com'}/jobs.rss").content).iter("item"):
        title = (item.findtext("title") or "").strip()
        places = [", ".join(filter(None, [(l.findtext(locations + "city") or "").strip(), (l.findtext(locations + "country") or "").strip()]))
                  for l in item.iter(locations + "location")]
        status = (item.findtext("remoteStatus") or "").lower()
        location = "; ".join(dict.fromkeys(p for p in places if p)) + (" (remote)" if status == "fully" else " (hybrid)" if status == "hybrid" else "")
        if role_of(title) and place_of(location):
            posted = str(parsedate_to_datetime(item.findtext("pubDate"))).split(" ")[0] if item.findtext("pubDate") else None
            text = BeautifulSoup(item.findtext("description") or "", "html.parser").get_text(" ", strip=True) or None
            rows.append(row(company.lower(), None, title, company, location, item.findtext("link"), posted, text,
                            {"title": title, "location": location, "guid": item.findtext("guid")}))
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
    is left out: many of your companies hire abroad too, unless the page names no place at all: then its jobs are the
    company's own, in Egypt)."""
    kept, SITE.page = getattr(SITE, "page", None), None
    soup = BeautifulSoup(kept[1] if kept and kept[0] == url else rendered(url), "html.parser")
    anywhere = place_of(soup.get_text(" ", strip=True))
    rows, urls = [], set()
    for a in soup.find_all("a", href=True):
        title, job_url = a.get_text(" ", strip=True), urljoin(url, a["href"])
        if not role_of(title) or re.search(r"/(?:category|tag)/", job_url):  # a category of a blog is not a job
            continue
        context, card = "", a
        for _ in range(3):  # the link's parent, or the card around it when the place sits beside the link (not a long list)
            card = card.parent
            text = card.get_text(" ", strip=True) if card else ""
            if card is None or card.name in ("body", "html", "[document]") or (context and len(text) > 400):
                break
            context = text
            if place_of(context):
                break
        place = place_of(context) or (None if anywhere else HOME)
        if place and job_url not in urls:
            urls.add(job_url)
            # the place its text names, not the text: Capgemini's link text is title, city and team in one line
            rows.append(row(company.lower(), None, title, company, place, job_url,
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
        page = get(f"{base}/search/", q="", locationsearch=location, sortColumn="referencedate", sortDirection="desc")
        landed = urlsplit(page.url)  # a sign-in or maintenance page answers 200 with no job: not a quiet day
        if landed.hostname != urlsplit(base).hostname or "/search/" not in landed.path:
            raise ValueError(f"{base}: its search led to {page.url}"[:200])
        soup = BeautifulSoup(page.text, "html.parser")
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


FEED_WORDS = ("engineer", "developer", "analyst", "analytics")  # a word of each of your roles' titles


def rss(company: str, feed: str) -> list[dict]:
    """A career site's RSS feed of its newest jobs (Deloitte Middle East on Avature). A feed of a search (search=Egypt)
    gives only the search's 20 newest, so it is asked again for each of FEED_WORDS ('engineer Egypt'); a job is one row."""
    rows, links = [], set()
    asked = re.search(r"search=([^&]*)", feed)
    feeds = [feed] + [feed.replace(asked.group(0), f"search={word}+{asked.group(1)}") for word in FEED_WORDS if asked]
    for one in feeds:
        for item in ET.fromstring(get(one).content).iter("item"):
            title, where, link = item.findtext("title", ""), item.findtext("description", ""), item.findtext("link", "")
            posted = str(parsedate_to_datetime(item.findtext("pubDate"))).split(" ")[0] if item.findtext("pubDate") else ""
            if link not in links and (not posted or posted >= since()) and role_of(title) and place_of(f"{where} {title}"):
                links.add(link)
                rows.append(row(company.lower(), None, title, company, where, link, posted or None, None,
                                {"title": title, "location": where, "link": link}))
    return rows


def pinpoint(company: str, slug: str) -> list[dict]:
    """Every open job of a company on Pinpoint, through its public postings.json."""
    rows = []
    for j in get(f"https://{slug}.pinpointhq.com/postings.json").json()["data"]:
        where = j.get("location") or {}
        location = ", ".join(filter(None, [where.get("city"), where.get("province"), where.get("name")]))
        if role_of(j["title"]) and place_of(location):
            rows.append(row(company.lower(), None, j["title"].strip(), company, location, j["url"], None,
                            BeautifulSoup(j.get("description") or "", "html.parser").get_text(" ", strip=True) or None, j))
    return rows


def oracle_hcm(company: str, url: str) -> list[dict]:
    """Every open job of a company on Oracle Recruiting (a careers address ending /sites/<site>/jobs), newest first,
    through its requisitions API: 50 a page, at most 5 pages. The address's own locationId (a country) is asked for."""
    parts = urlsplit(html.unescape(url))
    language, site = re.search(r"/CandidateExperience/(\w+)/sites/([\w-]+)", parts.path).groups()
    location_id = (parse_qs(parts.query).get("locationId") or [None])[0]
    rows, seen = [], set()
    for offset in range(0, 250, 50):
        finder = f"findReqs;siteNumber={site},limit=50,offset={offset},sortBy=POSTING_DATES_DESC" + (f",locationId={location_id}" if location_id else "")
        item = fetch("GET", f"https://{parts.netloc}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
                            f"?onlyData=true&expand=requisitionList&finder={finder}").json()["items"][0]
        for j in item.get("requisitionList") or []:
            location = (j.get("PrimaryLocation") or "") + (" (remote)" if "remote" in (j.get("WorkplaceType") or "").lower() else "")
            if j["Id"] not in seen and role_of(j["Title"]) and place_of(location):
                seen.add(j["Id"])
                rows.append(row(company.lower(), None, j["Title"], company, location,
                                f"https://{parts.netloc}/hcmUI/CandidateExperience/{language}/sites/{site}/job/{j['Id']}",
                                j.get("PostedDate"), BeautifulSoup(j.get("ShortDescriptionStr") or "", "html.parser").get_text(" ", strip=True) or None, j))
        if offset + 50 >= (item.get("TotalJobsCount") or 0):
            break
    return rows


def eightfold(company: str, url: str) -> list[dict]:
    """An Eightfold career site (Vodafone): its jobs of the address's location, newest first, 10 a page, through its
    search API (the address names its tenant: ...?location=Egypt&domain=vodafone.com)."""
    parts = urlsplit(html.unescape(url))
    query, rows, start = parse_qs(parts.query), [], 0
    while start < 100:
        data = fetch("GET", f"{parts.scheme}://{parts.netloc}/api/pcsx/search?" + urlencode(
            {"domain": query["domain"][0], "location": (query.get("location") or [""])[0], "start": start, "sort_by": "timestamp"})).json()["data"]
        for p in data.get("positions") or []:
            location = p.get("location") or ", ".join(p.get("locations") or [])
            if p.get("workLocationOption") in ("remote", "hybrid"):
                location += f" ({p['workLocationOption']})"
            if role_of(p["name"]) and place_of(location):
                posted = str(datetime.fromtimestamp(p["postedTs"], timezone.utc).date()) if p.get("postedTs") else None
                rows.append(row(company.lower(), None, p["name"], company, location, f"{parts.scheme}://{parts.netloc}{p['positionUrl']}",
                                posted, None, p))
        start += 10
        if start >= (data.get("count") or 0):
            break
    return rows


def ibm(company: str, url: str) -> list[dict]:
    """IBM's jobs in the country its careers address names (field_keyword_05[0]=Egypt), newest first, through the search API
    its careers page asks (the page builds its list in the browser): 30 a page, at most 5 pages."""
    country = (parse_qs(urlsplit(html.unescape(url)).query).get("field_keyword_05[0]") or ["Egypt"])[0]
    rows = []
    for start in range(0, 150, 30):
        filter_ = {"term": {"field_keyword_05": country}}
        data = fetch("POST", "https://www-api.ibm.com/search/api/v2", headers={"Accept": "application/json"}, json={
            "appId": "careers", "scopes": ["careers2"], "query": {"bool": {"must": [filter_]}}, "post_filter": filter_,
            "size": 30, "from": start, "sort": [{"dcdate": "desc"}, {"_score": "desc"}], "lang": "zz", "localeSelector": {},
            "sm": {"query": "", "lang": "zz"},
            "_source": ["title", "url", "description", "dcdate", "field_keyword_05", "field_keyword_17"]}).json()["hits"]
        for hit in data["hits"]:
            j = hit["_source"]
            location = j.get("field_keyword_05", "") + (f" ({j['field_keyword_17'].lower()})" if j.get("field_keyword_17") in ("Hybrid", "Remote") else "")
            if role_of(j["title"]) and place_of(location):
                rows.append(row(company.lower(), None, j["title"], company, location, j["url"], j.get("dcdate"), j.get("description"), j))
        if start + 30 >= data["total"]["value"]:
            break
    return rows


def zoho(company: str, url: str) -> list[dict]:
    """Every open job of a company on Zoho Recruit: its careers page carries them as JSON in one input (id=jobs)."""
    box = BeautifulSoup(get(url).text, "html.parser").find("input", id="jobs")
    if box is None:
        raise ValueError(f"{url}: its page no longer carries the jobs input")
    rows = []
    for j in json.loads(box["value"]):
        location = ", ".join(filter(None, [j.get("City"), j.get("Country")]))
        if j.get("Publish") and role_of(j["Posting_Title"]) and place_of(location):
            rows.append(row(company.lower(), None, j["Posting_Title"], company, location, f"{url}/{j['id']}", j.get("Date_Opened"),
                            BeautifulSoup(j.get("Job_Description") or "", "html.parser").get_text(" ", strip=True) or None, j))
    return rows


def taleo(company: str, base: str) -> list[dict]:
    """Every open job of a company on Taleo (base: https://<host>/careersection/<section>), newest first, through its REST
    search, 25 a page, at most 8 pages. The portal id it asks with is in the section's search page. A classic section with
    no REST search (e& Egypt: no portal id) lists its newest 100 jobs in that page itself: they are read from there."""
    host = base.split("/careersection/")[0]
    page = get(f"{base}/jobsearch.ftl", lang="en", dropListSize=100).text
    portal = re.search(r"portal(?:No)?[=:]\s*'?(\d{6,})", page)
    if not portal:
        if listed := re.search(r"api\.fillList\('requisitionListInterface', 'listRequisition', (\[.*?\])\);", page, re.S):
            return taleo_classic(company, base, ast.literal_eval(listed.group(1)))
        raise ValueError(f"{base}: its search page names no portal id")
    rows, seen = [], set()
    for number in range(1, 9):
        body = {"multilineEnabled": False, "sortingSelection": {"sortBySelectionParam": "3", "ascendingSortingOrder": "false"},
                "fieldData": {"fields": {"KEYWORD": "", "LOCATION": ""}, "valid": True},
                "filterSelectionParam": {"searchFilterSelections": []},
                "advancedSearchFiltersSelectionParam": {"searchFilterSelections": []}, "pageNo": number}
        data = fetch("POST", f"{host}/careersection/rest/jobboard/searchjobs?lang=en&portal={portal.group(1)}", json=body,
                     headers={"Accept": "application/json", "tz": "GMT+02:00", "X-Requested-With": "XMLHttpRequest"}).json()
        for j in data.get("requisitionList") or []:
            title, places = j["column"][0].strip(), json.loads(j["column"][1]) if len(j["column"]) > 1 else []
            location = places[0] if places else ""
            if j["contestNo"] not in seen and role_of(title) and place_of(location):
                seen.add(j["contestNo"])
                rows.append(row(company.lower(), None, title, company, location, f"{base}/jobdetail.ftl?job={j['contestNo']}",
                                None, None, j))
        paging = data.get("pagingData") or {}
        if number * (paging.get("pageSize") or 25) >= (paging.get("totalCount") or 0):
            break
    return rows


def taleo_classic(company: str, base: str, fields: list[str]) -> list[dict]:
    """A classic Taleo page's job list: 35 fields a job (3: its number, 4: its title, URL-encoded, 13: posted, 09/Nov/2026).
    It names no place: the section is the company's own in Egypt (e& Egypt), so HOME, as for SuccessFactors."""
    rows = []
    for i in range(0, len(fields) - 34, 35):
        number, title, posted = fields[i + 3], unquote(fields[i + 4]).strip(), fields[i + 13]
        if role_of(title):
            day = datetime.strptime(posted, "%d/%b/%Y").date().isoformat() if posted else None
            rows.append(row(company.lower(), None, title, company, HOME, f"{base}/jobdetail.ftl?job={number}", day, None,
                            {"contestNo": number}))
    return rows


def workday(company: str, base: str) -> list[dict]:
    """A company's jobs in Egypt on a Workday site, through its cxs search, 20 a page, at most 10 pages. Its address is
    on myworkdaysite.com (/recruiting/<tenant>/<site>) or on myworkdayjobs.com (<tenant>.wdN..., then the site, after a
    language such as en-US when it has one): the 28,000-company feed gave none of PwC's Egypt jobs (2026-11-12)."""
    host, tenant, site = (re.match(r"https://([\w.-]+)/recruiting/([\w-]+)/([\w-]+)", base)
                          or re.match(r"https://(([\w-]+)\.wd\d+\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)", base)).groups()
    rows = []
    for offset in range(0, 200, 20):
        data = fetch("POST", f"https://{host}/wday/cxs/{tenant}/{site}/jobs", headers={"Accept": "application/json"},
                     json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": "Egypt"}).json()
        for j in data.get("jobPostings") or []:
            location = " ".join((j.get("locationsText") or "").split())
            if role_of(j["title"]) and place_of(location):
                ago = j.get("postedOn", "").lower().replace("posted", "").strip()
                posted = date.today() if ago == "today" else relative_date(ago)
                rows.append(row(company.lower(), None, j["title"], company, location, base + j["externalPath"],
                                str(posted) if posted else None, None, j))
        if offset + 20 >= (data.get("total") or 0):
            break
    return rows


READERS = {"workable": workable, "greenhouse": greenhouse, "lever": lever, "ashby": ashby, "phenom": phenom,
           "smartrecruiters": smartrecruiters, "recruitee": recruitee, "personio": personio, "teamtailor": teamtailor,
           "successfactors": successfactors, "successfactors_classic": successfactors_classic, "rss": rss, "page": career_page,
           "pinpoint": pinpoint, "oracle_hcm": oracle_hcm, "eightfold": eightfold, "zoho": zoho, "taleo": taleo, "workday": workday, "ibm": ibm}
