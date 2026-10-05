"""The Gulf's boards: NaukriGulf, GulfTalent, Dubizzle Jobs. Outside Egypt only remote jobs are kept."""

from __future__ import annotations

import re
import time
from datetime import date, datetime, timezone

from ..config import EXTRACT_SECONDS, KEYWORDS, REMOTE, place_of, role_of
from .base import Held, changed, fetch, get, row, time_left, week


def naukrigulf() -> list[dict]:
    """NaukriGulf (the Gulf), through the search API its web app calls; it drops plain requests, so
    request() asks it as a real Chrome. The 30 newest jobs of each keyword, from the last week (its
    dates run days late). It has no remote filter: a job whose title or summary says remote is marked
    so, and outside Egypt only those are kept (your rule)."""
    api = {"appid": "205", "systemid": "2323", "accept": "application/json"}
    rows, urls, read = [], set(), 0
    for keyword in KEYWORDS:
        if time_left(EXTRACT_SECONDS) < 15:
            print("naukrigulf: time budget reached, the rest waits for the next run")
            break
        try:
            jobs = fetch("GET", "https://www.naukrigulf.com/spapi/jobapi/search", headers=api, timeout=30, params={
                "Keywords": keyword, "Limit": 30, "Offset": 0, "SortPreference": "date", "pageNo": 1}).json()["Jobs"]
            read += len(jobs)
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
    if not read:
        changed("naukrigulf", "its search API returned no jobs for any keyword")
    return rows


def gulftalent() -> list[dict]:
    """GulfTalent (the Gulf), through the search API its pages call; it turns plain requests away, so
    request() asks it as a real Chrome. The 25 newest jobs of each keyword (it matches the whole text,
    so the title rules pick yours), from the last week; its remote filter returns nothing, so its
    is_remote mark decides, and outside Egypt only remote jobs are kept (your rule)."""
    rows, urls, read = [], set(), 0
    for keyword in KEYWORDS:
        if time_left(EXTRACT_SECONDS) < 15:
            print("gulftalent: time budget reached, the rest waits for the next run")
            break
        try:
            jobs = get("https://www.gulftalent.com/api/jobs/search", version=2, search_keyword=keyword, search_order="d",
                       limit=25, offset=0, include_scraped=1).json()["results"]["data"]
            read += len(jobs)
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
    if not read:
        changed("gulftalent", "its search API returned no jobs for any keyword")
    return rows


def dubizzle() -> list[dict]:
    """Dubizzle Jobs (UAE), through the public search index (Algolia) its pages query - the pages
    themselves sit behind a bot check: its remote jobs of the last week, newest first, in one call."""
    hits = fetch("POST", "https://WD0PTZ13ZS-dsn.algolia.net/1/indexes/by_added_desc_jobs.com/query",
                 headers={"x-algolia-application-id": "WD0PTZ13ZS",
                          "x-algolia-api-key": "cdd839b4fdac840289e88633779e8634"},
                 json={"hitsPerPage": 100, "filters": f'added > {int(time.time()) - 7 * 86400}'
                                                      ' AND "details.Remote Job.en.value":"Yes"'}).json()["hits"]
    if not hits:
        changed("dubizzle", "its search index returned no remote jobs this week")
    rows = []
    for h in hits:
        title = (h.get("name") or {}).get("en") or ""
        location = ", ".join(reversed((h.get("location_list") or {}).get("en") or ["UAE"])) + " (remote)"
        if role_of(title) and place_of(location):
            company = (((h.get("details") or {}).get("Company Name") or {}).get("en") or {}).get("value")
            rows.append(row("dubizzle", None, title, company, location, h["absolute_url"]["en"],
                            str(date.fromtimestamp(int(h["created_at"]))), None, h))
    return rows
