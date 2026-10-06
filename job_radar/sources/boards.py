"""The big job boards and job searches: Indeed and Bayt (JobSpy), Workable's search, freehire.me, Jooble."""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor

from bs4 import BeautifulSoup

from ..config import BAYT_PLACES, EXTRACT_SECONDS, HOURS_OLD, KEYWORDS, ONSITE_PLACES, PLACES, ROLES, place_of, role_of
from .base import ERRORS, Held, duration, fetch, get, hold, row, since, time_left, waiting


class Blocked(logging.Handler):
    """JobSpy logs a board's 403 or 429 and returns no jobs: this notes which board it was."""
    def __init__(self) -> None:
        super().__init__()
        self.boards: set[str] = set()

    def emit(self, record: logging.LogRecord) -> None:
        if re.search(r"status code (403|429)", record.getMessage()):
            self.boards.add(record.name.split(":")[-1].lower())


BLOCKED = Blocked()


def indeed() -> list[dict]:
    """Indeed through JobSpy: every role in every place, remote jobs only outside Egypt
    (ONSITE_PLACES), 6 places at a time, one place per call (one down is a short day)."""
    with ThreadPoolExecutor(6) as pool:
        return [r for rows in pool.map(lambda place: board("indeed", [place]), PLACES) for r in rows]


def bayt() -> list[dict]:
    """Bayt through JobSpy, in its own task: it has jobs only in Egypt and the Gulf, searched one at a
    time. Egypt first, then the Gulf places in a new order each run, so none is always the one cut at
    the time budget."""
    places = [p for p in PLACES if p[0] in BAYT_PLACES]
    return board("bayt", places[:1] + random.sample(places[1:], len(places) - 1))


def board(site: str, places: list[tuple]) -> list[dict]:
    """One JobSpy board's searches: each role in each place, 3 seconds apart. A board that answers
    403 or 429 gets no more searches for 6 hours (JobSpy gives no Retry-After). Searches stop at the
    step's time budget. LinkedIn is never searched: its jobs come only from your alert emails."""
    # JobSpy loads pandas and pyarrow (~5 s and ~80 MB per process), so only the two steps that use
    # it import it. BLOCKED goes on after JobSpy has set up its own log output (adding it twice is a no-op).
    from jobspy import scrape_jobs
    for name in ("Indeed", "Bayt"):
        logging.getLogger(f"JobSpy:{name}").addHandler(BLOCKED)
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
