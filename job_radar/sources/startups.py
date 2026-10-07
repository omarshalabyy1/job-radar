"""The startup job platforms: Wellfound, Welcome to the Jungle, startup.jobs, Built In and Y Combinator's
board. Wellfound and Welcome to the Jungle also give the company's size (payload company_size)."""

from __future__ import annotations

import html
import json
import re
from datetime import date, datetime, timedelta, timezone

from bs4 import BeautifulSoup

from ..config import EXTRACT_SECONDS, KEYWORDS, role_of
from .base import Held, changed, fetch, get, row, since, time_left

WELLFOUND_ROLES = ["data-engineer", "data-analyst", "data-scientist", "machine-learning-engineer",
                   "artificial-intelligence-engineer", "business-intelligence-analyst"]
WELLFOUND_PAGES = 3  # its pages are sorted by relevance; the jobs of the last day sit in the first few
STARTUP_JOBS_ROLES = ["data-engineer", "analytics-engineer", "data-analyst", "data-scientist",
                      "machine-learning-engineer", "ai-engineer"]
YC_PAGES = ["data-science", "data-science/remote", "software-engineer", "software-engineer/remote"]
# Welcome to the Jungle searches with Algolia; its public search key is in the page's /api/env
WTTJ_ALGOLIA = {"X-Algolia-Application-Id": "CSEKHVMS53", "X-Algolia-API-Key": "4bd8f6215d0cc52b26430765769e65a0",
                "Referer": "https://www.welcometothejungle.com/", "Content-Type": "application/json"}
WTTJ_SEARCH = "https://csekhvms53-dsn.algolia.net/1/indexes/wttj_jobs_production_en_published_at_desc/query"


def out_of_time() -> bool:
    return time_left(EXTRACT_SECONDS) < 5


def wellfound() -> list[dict]:
    """Wellfound's role pages (remote, then Egypt): the jobs and their startups are in the page's
    __NEXT_DATA__, each startup with its size ('SIZE_51_200')."""
    rows, read, seen = [], 0, set()
    for role in WELLFOUND_ROLES:
        urls = [f"https://wellfound.com/role/l/{role}/egypt"] + [
            f"https://wellfound.com/role/r/{role}" + (f"?page={n}" if n > 1 else "") for n in range(1, WELLFOUND_PAGES + 1)]
        for url in urls:
            if out_of_time():
                return rows
            try:
                page = get(url).text
            except Held as e:  # keep what the pages before it found
                print(f"wellfound: {e}")
                return rows
            blob = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
            if not blob:
                continue
            data = json.loads(blob.group(1))["props"]["pageProps"]["apolloState"]["data"]
            for startup in (v for v in data.values() if v.get("__typename") == "StartupResult"):
                size = (startup.get("companySize") or "").removeprefix("SIZE_").replace("_PLUS", "+").replace("_", "-")
                for ref in startup.get("highlightedJobListings") or []:
                    job = data.get(ref["__ref"]) or {}
                    read += 1
                    if not job.get("liveStartAt") or job["id"] in seen:
                        continue
                    seen.add(job["id"])
                    posted = str(datetime.fromtimestamp(job["liveStartAt"], timezone.utc).date())
                    if posted < since() or not role_of(job["title"]):
                        continue
                    where = ", ".join(job.get("acceptedRemoteLocationNames") or job.get("locationNames") or [])
                    # no countries named is not "worldwide": such a job stays an Unknown location, kept as remote
                    location = (f"Remote: {where}" if where else "Remote") if job.get("remote") else where
                    rows.append(row("wellfound", None, job["title"].strip(), startup["name"], location,
                                    f"https://wellfound.com/jobs/{job['id']}-{job['slug']}", posted, job.get("description"),
                                    {**job, "is_remote": bool(job.get("remote")), "company_size": size or None}))
    if not read:
        changed("wellfound", "its role pages had no jobs")
    return rows


def welcometothejungle() -> list[dict]:
    """Welcome to the Jungle's Algolia index, newest first: fully remote jobs whose title has a search
    word, with the company's headcount (nb_employees)."""
    rows, read = [], 0
    for keyword in KEYWORDS:
        if out_of_time():
            return rows
        try:
            query = {"query": keyword, "hitsPerPage": 100, "filters": "remote:fulltime",
                     "restrictSearchableAttributes": ["name"]}
            hits = fetch("POST", WTTJ_SEARCH, headers=WTTJ_ALGOLIA, data=json.dumps(query)).json()["hits"]
        except Held as e:
            print(f"welcometothejungle: {e}")
            return rows
        read += len(hits)
        for h in hits:
            posted = h["published_at"][:10]
            if posted < since():
                break  # newest first: the rest is older
            if role_of(h["name"]):
                org = h["organization"]
                countries = ", ".join(o["country"] for o in h.get("offices") or [] if o.get("country"))
                rows.append(row("welcometothejungle", None, h["name"], org["name"], f"Remote: {countries or 'Worldwide'}",
                                f"https://www.welcometothejungle.com/en/companies/{org['slug']}/jobs/{h['slug']}", posted,
                                "\n\n".join(filter(None, [h.get("summary"), h.get("profile")])),
                                {**h, "is_remote": True,
                                 "company_size": str(org["nb_employees"]) if org.get("nb_employees") else None}))
    if not read:
        changed("welcometothejungle", "its search returned no jobs for any keyword")
    return rows


def startup_jobs() -> list[dict]:
    """startup.jobs' role pages, newest first, 20 a page, until a page reaches past the window."""
    rows, read = [], 0
    for role in STARTUP_JOBS_ROLES:
        for n in range(1, 6):
            if out_of_time():
                return rows
            try:
                page = get(f"https://startup.jobs/roles/{role}", **({"page": n} if n > 1 else {})).text
            except Held as e:
                print(f"startup_jobs: {e}")
                return rows
            stamps = BeautifulSoup(page, "html.parser").select('time[data-post-template-target="timestamp"]')
            read += len(stamps)
            for stamp in stamps:
                card = stamp.find_parent(attrs={"data-mark-visited-links-target": "container"})
                title = card and card.select_one('[data-post-template-target="title"]')
                if not title or stamp["datetime"][:10] < since() or not role_of(title.get_text(strip=True)):
                    continue
                company = card.select_one('[data-post-template-target="companyName"]')
                location = card.select_one('[data-post-template-target="location"]')
                rows.append(row("startup_jobs", None, title.get_text(strip=True), company and company.get_text(strip=True),
                                location and location.get_text(" ", strip=True), "https://startup.jobs" + title["href"],
                                stamp["datetime"][:10], None, {"posted_at": stamp["datetime"], "role_page": role}))
            if not stamps or min(s["datetime"][:10] for s in stamps) < since():
                break  # newest first: the next page is older
    if not read:
        changed("startup_jobs", "its role pages had no jobs")
    return rows


def card_age(text: str) -> date | None:
    """'Yesterday', '5 Hours Ago', 'Reposted 2 Days Ago' (Built In), '6 days', 'about 3 hours' (Y Combinator)."""
    text = text.lower()
    if re.search(r"just now|today|minute|hour", text):
        return date.today()
    if "yesterday" in text or re.search(r"\b(a|1) day\b", text):
        return date.today() - timedelta(days=1)
    if m := re.search(r"(\d+) days?", text):
        return date.today() - timedelta(days=int(m.group(1)))
    return None  # weeks, months, years: older than any window


def builtin() -> list[dict]:
    """Built In's remote data and analytics jobs, newest first, 10 a page; a few new ones a day."""
    rows, cards = [], []
    for n in (1, 2):
        try:
            cards = BeautifulSoup(get("https://builtin.com/jobs/remote/data-analytics", page=n).text,
                                  "html.parser").select('[data-id="job-card"]')
        except Held as e:
            print(f"builtin: {e}")
            return rows
        if not cards and n == 1:
            changed("builtin", "no job cards on jobs/remote/data-analytics")
        ages = []
        for card in cards:
            words = card.get_text("|", strip=True).split("|")
            age = card_age(next((w for w in words if re.search(r"ago|yesterday|today", w, re.I)), ""))
            ages.append(age)
            title, company = card.select_one('[data-id="job-card-title"]'), card.select_one('[data-id="company-title"]')
            if not (title and age) or str(age) < since() or not role_of(title.get_text(strip=True)):
                continue
            mode = next((w for w in words if re.fullmatch(r"(Remote|Hybrid|In-Office)( or \w+(-\w+)?)?", w)), "")
            where = words[words.index(mode) + 1] if mode and words.index(mode) + 1 < len(words) else ""
            rows.append(row("builtin", None, title.get_text(strip=True), company and company.get_text(strip=True),
                            f"{mode}: {where}" if mode else where, "https://builtin.com" + title["href"], str(age), None,
                            {"card": words, "is_remote": mode == "Remote"}))
        if any(a is None or str(a) < since() for a in ages):
            break
    return rows


def ycombinator() -> list[dict]:
    """Y Combinator's job board (Work at a Startup): each page's jobs are in its Inertia data-page JSON,
    with their age ('6 days') rather than a date."""
    rows, read = [], 0
    for name in YC_PAGES:
        try:
            page = get(f"https://www.ycombinator.com/jobs/role/{name}").text
        except Held as e:
            print(f"ycombinator: {e}")
            return rows
        blob = re.search(r'data-page="([^"]+)"', page)
        jobs = json.loads(html.unescape(blob.group(1)))["props"].get("jobPostings", []) if blob else []
        read += len(jobs)
        for j in jobs:
            age = card_age(j.get("createdAt") or "")
            if age and str(age) >= since() and role_of(j["title"]):
                rows.append(row("ycombinator", None, j["title"], j["companyName"], j.get("location"),
                                "https://www.ycombinator.com" + j["url"], str(age), None,
                                {**j, "is_remote": "remote" in (j.get("location") or "").lower()}))
    if not read:
        changed("ycombinator", "its role pages had no jobs")
    return rows
