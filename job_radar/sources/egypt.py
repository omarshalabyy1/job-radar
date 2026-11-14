"""Egypt's own boards: Wuzzuf (every job of the day), Tanqeeb (its Egypt and Gulf sites) and Joblyin."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from bs4 import BeautifulSoup

from ..config import EXTRACT_SECONDS, HOURS_OLD, TANQEEB_PAGES, TANQEEB_SITES, kept, place_of
from .base import BROWSER, Held, changed, fetch, get, held, relative_date, row, since, time_left


WUZZUF_HOURS = 3 * HOURS_OLD  # three days: a run that came late or was missed (the laptop was off) still finds the
# postings of the days it missed, which are on the site for weeks and which no later run would read. Free to re-read:
# a posting already stored only has its last_seen moved (steps.load), and Wuzzuf's three days are about 170 jobs
# (Egypt's Friday and Saturday are quiet), one page more than one day's 130.
WUZZUF_PAGES = 40  # 2000 jobs at 50 a page: the most one run reads, which the site's 5000-odd would need 100 pages for
WUZZUF_STOPS = {"time budget": "time budget reached", "page limit": f"page limit of {WUZZUF_PAGES} pages reached",
                "held": "the site held us", "list ended": "the list ended early", "no dates": "a page had no posting dates",
                "no jobs": "the site listed no jobs"}


def wuzzuf() -> list[dict]:
    """Wuzzuf, through the JSON API its own web app calls (robots.txt allows it; only its search
    page sits behind Cloudflare's challenge, and this does not touch it): every job in Egypt,
    newest first, 50 a page with their details 20 per call, until a page has no job from the last
    WUZZUF_HOURS hours (its own "last 24 hours" filter stops at 50 jobs, too few on a busy day). The
    window is counted from Wuzzuf's newest job, so its time zone does not matter; 1 second between
    calls. The title rules keep yours; the board's own remote flag goes on with the posting (is_remote).
    It prints how many jobs the site reported against how many it read, and a WARNING when the read
    ended for any reason but the window (the time budget, the page limit, a hold, a list that ended
    early), so a truncation shows in the run log."""
    api = {**BROWSER, "Content-Type": "application/vnd.api+json", "Accept": "application/vnd.api+json"}
    rows, cutoff, oldest = [], None, None
    reported, read, pages, no_details = None, 0, 0, 0
    stopped = "page limit"  # what ends the loop when nothing else did
    for start in range(0, WUZZUF_PAGES * 50, 50):
        if time_left(EXTRACT_SECONDS) < 60:  # Tanqeeb comes after it in the same step
            stopped = "time budget"
            break
        body = {"startIndex": start, "pageSize": 50, "longitude": "0", "latitude": "0", "query": "", "searchFilters": {}}
        try:
            page = fetch("POST", "https://wuzzuf.net/api/search/job", data=json.dumps(body), headers=api, timeout=30).json()
            hits = page.get("data", [])
            if not start:
                reported = (page.get("meta") or {}).get("totalResultsCount")
                if not hits:
                    changed("wuzzuf", "its search API returned no jobs")
                elif reported is None:
                    changed("wuzzuf", "its search API gave no result count")
            if not hits:
                stopped = "list ended" if start else "no jobs"
                break
            companies = {hit["id"]: ({f["name"]: f["value"] for f in hit["attributes"].get("computedFields", [])}
                                     .get("company_name") or [""])[0].strip() for hit in hits}
            ids, jobs = list(companies), []
            for i in range(0, len(ids), 20):
                time.sleep(1)
                jobs += fetch("GET", "https://wuzzuf.net/api/job", params={"filter[other][ids]": ",".join(ids[i:i + 20])},
                              headers=api, timeout=30).json().get("data", [])
        except Held as e:  # keep what the pages before it found
            held("wuzzuf", e)
            stopped = "held"
            break
        read, pages, no_details = read + len(ids), pages + 1, no_details + len(ids) - len(jobs)
        posted_at = {job["id"]: datetime.strptime(job["attributes"]["postedAt"], "%m/%d/%Y %H:%M:%S")
                     for job in jobs if job["attributes"].get("postedAt")}
        if not posted_at:
            stopped = "no dates"
            break
        oldest = min(posted_at.values()) if oldest is None else min(oldest, *posted_at.values())
        cutoff = cutoff or max(posted_at.values()) - timedelta(hours=WUZZUF_HOURS)
        recent = [job for job in jobs if posted_at.get(job["id"], cutoff) > cutoff]
        for job in recent:
            a, where = job["attributes"], job["attributes"].get("location") or {}
            location = ", ".join(filter(None, [(where.get("city") or {}).get("name"), (where.get("country") or {}).get("name")]))
            if kept(a["title"]) and place_of(location):
                text = BeautifulSoup(f"{a.get('description') or ''} {a.get('requirements') or ''}", "html.parser")
                remote = ((a.get("workplaceArrangement") or {}).get("displayedName") or "").lower() == "remote"
                rows.append(row("wuzzuf", None, a["title"], companies[job["id"]], location, f"https://wuzzuf.net/{a['uri']}",
                                str(posted_at[job["id"]].date()), text.get_text(" ", strip=True), {**job, "is_remote": remote}))
        if not recent:  # newest first: a page with nothing from the window ends it
            stopped = "window"
            break
        time.sleep(1)
    wuzzuf_report(reported, read, pages, oldest, stopped, len(rows), no_details)
    return rows


def wuzzuf_report(reported: int | None, read: int, pages: int, oldest: datetime | None, stopped: str, kept_rows: int,
                  no_details: int) -> None:
    """The run log's account of one Wuzzuf read: what the site said it lists, what was read and why it stopped. The window
    ending the read is the normal stop (a day's few hundred jobs of the site's five thousand): a plain line. Any other stop
    with fewer read than reported is a WARNING, as is a job the search listed whose details never came."""
    if no_details:
        print(f"WARNING wuzzuf: {no_details} of the {read} jobs the search listed came back without details")
    shown = f"site lists {reported} jobs" if reported is not None else "site gave no job count"
    done = f"read {read} in {pages} page{'s' * (pages != 1)}"
    if stopped == "window":
        down_to = f" down to {oldest:%Y-%m-%d %H:%M}" if oldest else ""
        print(f"wuzzuf: {shown}; {done}{down_to}, stopped at the {WUZZUF_HOURS}-hour window; kept {kept_rows}")
    elif reported is None or read >= reported:  # nothing to say it was cut short
        print(f"wuzzuf: {shown}; {done}, {WUZZUF_STOPS[stopped]}; kept {kept_rows}")
    else:
        print(f"WARNING wuzzuf: {shown} but read only {read} in {pages} page{'s' * (pages != 1)} ({WUZZUF_STOPS[stopped]}): "
              f"the {WUZZUF_HOURS}-hour window was not read to its end; kept {kept_rows}")


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
            links = soup.select("a.search-job-title-link")
            if not links:
                changed("tanqeeb", f"no job cards on {site}.tanqeeb.com/s/jobs/{page}")
            for link in links:
                card = link.find_parent("div", class_="search-job-card-top").parent
                title, posted = link.get_text(" ", strip=True), relative_date(text(card, ".search-job-date"))
                if posted and str(posted) >= since() and kept(title):
                    rows.append(row("tanqeeb", place, title, text(card, ".search-job-company-name"),
                                    text(card, ".search-job-workplace-location"),
                                    f"https://{site}.tanqeeb.com{link['href']}", str(posted), None,
                                    {"board": text(card, ".search-job-source"), "card": card.get_text(" | ", strip=True)}))
            time.sleep(1)
        return rows

    with ThreadPoolExecutor(len(TANQEEB_SITES)) as pool:
        return [r for rows in pool.map(read_site, TANQEEB_SITES, TANQEEB_SITES.values()) for r in rows]


JOBLYIN_HOURS = WUZZUF_HOURS  # three days, for the same reason: a late or missed run still finds the days it missed


def joblyin() -> list[dict]:
    """Joblyin, which reposts LinkedIn and Forasna jobs in Egypt (and a few in the Gulf): its whole job list, newest
    first, 10 cards a page, until a page has nothing from the last JOBLYIN_HOURS hours or links to no next page; each
    card's title, company, place, date and the board it came from. The job page's JobPosting gives describe its text.
    Its sitemap's 9,000-odd other job pages are listings over 14 days old (checked 2026-11-14)."""
    start, rows, page = str(date.today() - timedelta(hours=JOBLYIN_HOURS)), {}, 1
    while True:
        if time_left(EXTRACT_SECONDS) < 15:
            print("joblyin: time budget reached, the rest waits for the next run")
            break
        try:
            # the bytes: its Content-Type names no charset, so requests would read its UTF-8 as Latin-1 (its <meta> says UTF-8)
            soup = BeautifulSoup(get("https://joblyin.com/jobs", page=page).content, "html.parser")
        except Held as e:  # keep what the pages before it found
            held("joblyin", e)
            break
        cards = soup.select("article.job-card")
        if not cards and page == 1:
            changed("joblyin", "no job cards on joblyin.com/jobs")
        recent = False
        for card in cards:
            stamp = card.select_one("time[datetime]")
            posted = (stamp["datetime"][:10] if stamp else  # recent cards; older ones say "Posted: Nov 2, 2026"
                      str(datetime.strptime(card.select_one(".posted-meta").get_text(" ", strip=True)[len("Posted:"):].strip(),
                                            "%b %d, %Y").date()))
            if posted < start:
                continue
            recent = True
            title = card.select_one(".job-title-text").get_text(" ", strip=True)
            href = card.select_one("a.view-job")["href"]
            if kept(title):
                company, place = card.select_one(".company"), card.select_one(".job-meta > span")
                rows[f"https://joblyin.com{href}"] = row(
                    "joblyin", None, title, company and company.get_text(" ", strip=True),
                    place and place.get_text(" ", strip=True).removeprefix("Location:").strip(), f"https://joblyin.com{href}",
                    posted, None, {"board": href.rsplit("/", 1)[1].rsplit("-", 1)[0],
                                   "remote": bool(card.select_one(".remote-badge")),
                                   "card": card.get_text(" | ", strip=True)})
        if not recent or not soup.select_one(f'a[href*="page={page + 1}"]'):  # the list goes on past the last page
            break
        page += 1
    return list(rows.values())  # once each: the list shifts while it is read
