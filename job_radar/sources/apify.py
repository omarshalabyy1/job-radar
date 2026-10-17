"""The hard job boards (LinkedIn, Indeed, Glassdoor, Wellfound, StepStone, Xing, SEEK) through Apify Actors, for the
website's collection: settings.yaml apify.boards names each board's Actor, its input and its caps, each enabled: false
until Omar subscribes (2026-10-17). The extract_apify step runs every enabled board once a run: it starts the Actor's run
with its maxItems and maxTotalChargeUsd (Apify stops the run at that cost), waits for it, and reads its dataset.

The budget guard: before each run, the account's spend this month (Apify's own figure, users/me/limits) plus the run's
cap must stay under apify.monthly_budget_usd (default $5, Apify's free credit); else the board waits for next month.
APIFY_TOKEN comes from .env and is never printed. Fallbacks with the same shape, not built (no key in either .env on
2026-10-17): Bright Data's Web Scraper API and datasets, and Firecrawl's search and extract (provider: brightdata or
firecrawl on a board would name them).

A row's source is 'apify:<board>', its natural key the job's own URL (raw.job_posting's (source, job_url)), so a run
read twice stores each job once. The Actors name their fields differently: the first field present wins (FIELDS)."""

from __future__ import annotations

import os
import time
from urllib.parse import parse_qsl, urlencode, urlsplit

import requests

from ..config import SETTINGS
from .base import row
from .market import text

API = "https://api.apify.com/v2"
# each job field, as the Actors name it: the first one a dataset item has
FIELDS = {"title": ("title", "jobTitle", "positionName", "position", "name"),
          "company": ("companyName", "company", "company_name", "employer", "companyDetails.name"),
          "location": ("location", "jobLocation", "formattedLocation", "locationName", "city"),
          "url": ("link", "url", "jobUrl", "job_url", "applyUrl", "externalApplyLink"),
          "posted": ("postedAt", "datePosted", "publishedAt", "postingDateParsed", "posted_at", "listedAt"),
          "description": ("descriptionText", "description", "jobDescription", "descriptionHtml")}
WAIT_SECONDS = 240  # a run still going after this is left to finish on Apify; its jobs are read by the next run


def field(item: dict, name: str):
    for key in FIELDS[name]:
        value = item
        for part in key.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if value not in (None, "", []):
            return value
    return None


ID_PARAMS = {"jk", "id", "jobid", "job_id", "currentjobid", "gh_jid", "vjk"}  # a link's query keys that name the job


def link(url: str) -> str:
    """A job's link without its tracking query, keeping the parameters that name the job (Indeed's jk): the natural key."""
    parts = urlsplit(url)
    keep = [(k, v) for k, v in parse_qsl(parts.query) if k.lower() in ID_PARAMS]
    return parts._replace(query=urlencode(keep), fragment="").geturl()


def normalize(board: str, items: list[dict]) -> list[dict]:
    """A dataset's items as wide postings: those with a title and a link, the link without its query (the natural key)."""
    rows = []
    for item in items:
        title, url = field(item, "title"), field(item, "url")
        if not (isinstance(title, str) and isinstance(url, str) and url.startswith("http")):
            continue
        body = field(item, "description")
        rows.append(row(f"apify:{board}", None, title.strip(), field(item, "company"), str(field(item, "location") or ""),
                        link(url), str(field(item, "posted") or "")[:10] or None,
                        text(body) if isinstance(body, str) and "<" in body else body,
                        {k: v for k, v in item.items() if k not in FIELDS["description"]}))
    return rows


def spent(token: str) -> float:
    """The account's spend this month, in dollars, as Apify counts it."""
    r = requests.get(f"{API}/users/me/limits", headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    return float(r.json()["data"]["current"]["monthlyUsageUsd"])


def run(token: str, actor: str, payload: dict, max_items: int, max_charge: float) -> list[dict]:
    """One Actor run, waited for, and its dataset's items."""
    auth = {"Authorization": f"Bearer {token}"}
    started = requests.post(f"{API}/acts/{actor.replace('/', '~')}/runs", headers=auth, json=payload, timeout=60,
                            params={"maxItems": max_items, "maxTotalChargeUsd": max_charge, "waitForFinish": 60})
    started.raise_for_status()
    info, deadline = started.json()["data"], time.monotonic() + WAIT_SECONDS
    while info["status"] in ("READY", "RUNNING") and time.monotonic() < deadline:
        info = requests.get(f"{API}/actor-runs/{info['id']}", headers=auth, params={"waitForFinish": 60}, timeout=90).json()["data"]
    items = requests.get(f"{API}/datasets/{info['defaultDatasetId']}/items", headers=auth, params={"clean": "true"}, timeout=120)
    items.raise_for_status()
    print(f"apify {actor}: run {info['status'].lower()}, {len(items.json())} items")
    return items.json()


def apify_jobs() -> list[dict]:
    """Every enabled board's run, inside the month's budget; none enabled (the default) reads nothing."""
    cfg = SETTINGS.get("apify") or {}
    boards = {name: b for name, b in (cfg.get("boards") or {}).items() if b.get("enabled")}
    if not boards:
        print("apify: no board enabled in settings.yaml (apify.boards)")
        return []
    token = (os.environ.get("APIFY_TOKEN") or "").strip()
    if not token:
        print("apify: no APIFY_TOKEN in .env, skipped")
        return []
    budget, rows = float(cfg.get("monthly_budget_usd", 5)), []
    for name, b in boards.items():
        used = spent(token)
        if used + float(b["max_charge_usd"]) > budget:
            print(f"apify {name}: ${used:.2f} spent this month, its run could pass the ${budget:.2f} budget: next month")
            continue
        rows += normalize(name, run(token, b["actor"], b["input"], int(b["max_items"]), float(b["max_charge_usd"])))
    return rows
