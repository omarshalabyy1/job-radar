"""The remote job boards: their APIs, feeds and pages."""

from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..config import EXTRACT_SECONDS, KEYWORDS, RELOMOTE_PAGES, REMOTE_OPEN_TO, place_of, role_of
from .base import Held, changed, get, hold, relative_date, row, since, time_left, week


def himalayas() -> list[dict]:
    """Himalayas' search API: remote jobs open to someone in Egypt, newest first, per keyword."""
    rows, read = [], 0
    for keyword in KEYWORDS:
        for offset in range(0, 100, 20):
            try:
                jobs = get("https://himalayas.app/jobs/api/search", q=keyword, country="EG", sort="recent",
                           offset=offset).json()["jobs"]
                read += len(jobs)
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
    if not read:
        changed("himalayas", "its search API returned no jobs for any keyword")
    return rows


def weworkremotely() -> list[dict]:
    """We Work Remotely's RSS feed of its latest jobs, kept when open to anywhere or EMEA."""
    rows, items = [], list(ET.fromstring(get("https://weworkremotely.com/remote-jobs.rss").content).iter("item"))
    if not items:
        changed("weworkremotely", "its feed has no jobs")
    for item in items:
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
    if not jobs:
        changed(source, "its feed returned no jobs")
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
    rows, urls = [], re.findall(r"<loc>(https://remote\.co/job-details/[^<]+)</loc>", sitemap)
    if not urls:
        changed("remoteco", "its sitemap lists no job pages")
    for url in urls:
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
        if not page:
            changed("remoteco", f"no job data (__NEXT_DATA__) on {url}")
            break
        j = json.loads(page.string)["props"]["pageProps"]["jobDetails"]
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
        if not cards and n == 1:
            changed("dailyremote", "no job cards on its first page")
        for card in cards:
            link, pill = card.select_one(".lst-card__title a"), card.select_one(".lst-card__meta .lst-pill")
            byline = card.select(".lst-card__byline span")
            posted = relative_date(byline[-1].get_text(strip=True)) if byline else None
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
            if not cards and n == 1:
                changed("relomote", f"no job cards on remote-jobs/{name}")
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
