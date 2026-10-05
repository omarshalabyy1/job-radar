"""Egypt's own boards: Wuzzuf (every job of the day) and Tanqeeb (its Egypt and Gulf sites)."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from bs4 import BeautifulSoup

from ..config import EXTRACT_SECONDS, HOURS_OLD, TANQEEB_PAGES, TANQEEB_SITES, place_of, role_of
from .base import BROWSER, Held, changed, fetch, get, relative_date, row, since, time_left


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
            if not hits and not start:
                changed("wuzzuf", "its search API returned no jobs")
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
            links = soup.select("a.search-job-title-link")
            if not links:
                changed("tanqeeb", f"no job cards on {site}.tanqeeb.com/s/jobs/{page}")
            for link in links:
                card = link.find_parent("div", class_="search-job-card-top").parent
                title, posted = link.get_text(" ", strip=True), relative_date(text(card, ".search-job-date"))
                if posted and str(posted) >= since() and role_of(title):
                    rows.append(row("tanqeeb", place, title, text(card, ".search-job-company-name"),
                                    text(card, ".search-job-workplace-location"),
                                    f"https://{site}.tanqeeb.com{link['href']}", str(posted), None,
                                    {"board": text(card, ".search-job-source"), "card": card.get_text(" | ", strip=True)}))
            time.sleep(1)
        return rows

    with ThreadPoolExecutor(len(TANQEEB_SITES)) as pool:
        return [r for rows in pool.map(read_site, TANQEEB_SITES, TANQEEB_SITES.values()) for r in rows]
