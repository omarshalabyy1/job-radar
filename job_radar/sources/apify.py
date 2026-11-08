"""The hard job boards (LinkedIn, Indeed, Glassdoor, Wellfound, StepStone, Xing, SEEK) through Apify Actors, for the
website's collection: settings.yaml apify.boards names each board's Actor, its input and its caps, each enabled: false
until Omar subscribes (2026-10-17). The extract_apify step runs every enabled board once a run: it starts the Actor's run
with its maxItems and maxTotalChargeUsd (Apify stops the run at that cost), waits for it, and reads its dataset.

The budget guard: APIFY_TOKENS in .env (comma-separated, else APIFY_TOKEN) names every Apify account. Before each run,
each account's spend this month and its own monthly limit (Apify's figures, users/me/limits) are read, and the run goes
to the account with the most left once its cap and apify.margin_usd are kept back; with none left the board waits for
next month. The tokens come from .env and are never printed. Fallbacks with the same shape, not built (no key in either .env on
2026-10-17): Bright Data's Web Scraper API and datasets, and Firecrawl's search and extract (provider: brightdata or
firecrawl on a board would name them).

A row's source is 'apify:<board>', its natural key the job's own URL (raw.job_posting's (source, job_url)), so a run
read twice stores each job once. The Actors name their fields differently: the first field present wins (FIELDS).

A board with a `posts:` block (x, reddit) returns posts, not listings: sources/social.py asks the Actor for the last
posts.fresh_days days, keeps only the posts that are hiring posts for one of your roles, and says how many it dropped and why.

Spend is in the run log: each run prints its cost (Apify's own usageTotalUsd for the run) next to its cap and the account's
month so far, a cost over the cap is a WARNING, and extract_apify ends with spend_line(), so the step's last line, which the
run log keeps, is what the collect cost."""

from __future__ import annotations

import os
import time
from urllib.parse import parse_qsl, urlencode, urlsplit

import requests

from ..config import SETTINGS
from . import social
from .base import duration, hold, row, waiting
from .market import text

API = "https://api.apify.com/v2"
# each job field, as the Actors name it: the first one a dataset item has
FIELDS = {"title": ("title", "jobTitle", "job_title", "positionName", "position", "name", "text"),
          "company": ("companyName", "company", "company_name", "employer.name", "company.name", "companyDetails.name", "organization",
                      "author.userName", "communityName"),
          "location": ("location", "jobLocation", "formattedLocation", "locationName", "city", "locations_derived",
                       "location.city", "location.countryName", "job_location.unknown"),
          "url": ("link", "url", "job_url", "jobUrl", "detailUrl", "applyUrl", "externalApplyLink", "postUrl"),
          "posted": ("postedAt", "datePosted", "publishedAt", "postingDateParsed", "posted_at", "listedAt", "postedDate",
                     "date_posted", "datePublished", "job_posted_date", "createdAt"),
          "description": ("descriptionText", "description", "description.text", "jobDescription", "job_description",
                          "descriptionHtml", "description_text", "body", "textSnippet", "text")}
WAIT_SECONDS = 240  # a run still going after this is left to finish on Apify; its jobs are read by the next run
SPEND: dict[str, float | None] = {}  # board -> what its run cost this collect (None: Apify gave no figure); see spend_line()


class Items(list):
    """A run's dataset items, with what the run cost: Apify's usageTotalUsd (the total the account pays, platform use and
    pay-per-event charges), None when it gave none."""
    cost: float | None = None


def field(item: dict, name: str):
    for key in FIELDS[name]:
        value = item
        for part in key.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if value not in (None, "", []) and not isinstance(value, dict):  # a dict names a nested key instead
            return value
    return None


# a link's query keys that name the job (Glassdoor's jobListingId and jl: without them its 84 jobs were one link)
ID_PARAMS = {"jk", "id", "jobid", "job_id", "currentjobid", "gh_jid", "vjk", "joblistingid", "jl"}


def link(url: str) -> str:
    """A job's link without its tracking query, keeping the parameters that name the job (Indeed's jk): the natural key."""
    parts = urlsplit(url)
    keep = [(k, v) for k, v in parse_qsl(parts.query) if k.lower() in ID_PARAMS]
    return parts._replace(query=urlencode(keep), fragment="").geturl()


def normalize(board: str, items: list[dict], base: str = "") -> list[dict]:
    """A dataset's items as wide postings: those with a title and a link, the link without its query (the natural key);
    a relative link (StepStone's) is joined to the board's link_base."""
    rows = []
    for item in items:
        title, url = field(item, "title"), field(item, "url")
        if base and isinstance(url, str) and url.startswith("/"):
            url = base.rstrip("/") + url
        if not (isinstance(title, str) and isinstance(url, str) and url.startswith("http")):
            continue
        body, posted, place = field(item, "description"), str(field(item, "posted") or "")[:10], field(item, "location")
        rows.append(row(f"apify:{board}", None, " ".join(title.split())[:200], field(item, "company"),
                        ", ".join(place) if isinstance(place, list) else str(place or ""), link(url),
                        posted if posted[:2].isdigit() else None,
                        text(body) if isinstance(body, str) and "<" in body else body,
                        {k: v for k, v in item.items() if k not in FIELDS["description"]}))
    return rows


def spent(token: str) -> tuple[float, float]:
    """The account's spend this month and its monthly limit, in dollars, as Apify counts them."""
    r = requests.get(f"{API}/users/me/limits", headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    data = r.json()["data"]
    return float(data["current"]["monthlyUsageUsd"]), float(data["limits"]["maxMonthlyUsageUsd"])


def tokens() -> list[str]:
    return [t.strip() for t in (os.environ.get("APIFY_TOKENS") or os.environ.get("APIFY_TOKEN") or "").split(",") if t.strip()]


def run(token: str, actor: str, payload: dict, max_items: int, max_charge: float) -> Items:
    """One Actor run, waited for, and its dataset's items (with the run's cost)."""
    auth = {"Authorization": f"Bearer {token}"}
    started = requests.post(f"{API}/acts/{actor.replace('/', '~')}/runs", headers=auth, json=payload, timeout=90,
                            params={"maxItems": max_items, "maxTotalChargeUsd": max_charge, "waitForFinish": 60})
    started.raise_for_status()
    info, deadline = started.json()["data"], time.monotonic() + WAIT_SECONDS
    while info["status"] in ("READY", "RUNNING") and time.monotonic() < deadline:
        info = requests.get(f"{API}/actor-runs/{info['id']}", headers=auth, params={"waitForFinish": 60}, timeout=90).json()["data"]
    items = requests.get(f"{API}/datasets/{info['defaultDatasetId']}/items", headers=auth, params={"clean": "true"}, timeout=120)
    items.raise_for_status()
    print(f"apify {actor}: run {info['status'].lower()}, {len(items.json())} items")
    out = Items(items.json())
    cost = info.get("usageTotalUsd")
    out.cost = float(cost) if isinstance(cost, (int, float)) else None
    return out


def spend_line() -> str | None:
    """What this collect's runs cost, in one line ('apify: spent $0.0579 on 2 runs (x $0.0123, reddit $0.0456)'); None when
    no run started."""
    if not SPEND:
        return None
    total, n = sum(c for c in SPEND.values() if c is not None), len(SPEND)
    parts = ", ".join(f"{name} ${c:.4f}" if c is not None else f"{name} unknown" for name, c in SPEND.items())
    return f"apify: spent ${total:.4f} on {n} run{'s' if n != 1 else ''} ({parts})"


def apify_jobs(seen: dict[str, set[str]] | None = None) -> list[dict]:
    """Every enabled board's run, inside the month's budget; none enabled (the default) reads nothing. `seen` is the text
    hash -> links of the social posts already stored (steps.extract_apify reads it), so a repost is dropped."""
    SPEND.clear()
    cfg = SETTINGS.get("apify") or {}
    boards = {name: b for name, b in (cfg.get("boards") or {}).items() if b.get("enabled")}
    if not boards:
        print("apify: no board enabled in settings.yaml (apify.boards)")
        return []
    accounts = tokens()
    if not accounts:
        print("apify: no APIFY_TOKENS or APIFY_TOKEN in .env, skipped")
        return []
    margin, every, rows = float(cfg.get("margin_usd", 0.5)), float(cfg.get("every_hours", 20)) * 3600, []
    for name, b in boards.items():
        if wait := waiting(f"apify-{name}"):  # one run a day, however many collects (GitHub's two, Airflow's)
            print(f"apify {name}: next run in {duration(wait)} (apify.every_hours)")
            continue
        cap, left, month = float(b["max_charge_usd"]), {}, {}
        for n, token in enumerate(accounts, 1):
            try:
                used, limit = spent(token)
            except requests.RequestException as e:
                print(f"WARNING apify: account {n}'s usage could not be read ({type(e).__name__}), skipped")
                continue
            left[n], month[n] = limit - used - margin - cap, (used, limit)
        best = max(left, key=left.get, default=None)
        if best is None:
            print(f"WARNING apify {name}: no account's usage could be read: not run")
            continue
        if left[best] < 0:
            print(f"apify {name}: no account has ${cap:.2f} left this month: next month")
            continue
        posts = b.get("posts")  # a social board: posts, not listings
        try:
            sent = social.prepare_input(posts["kind"], b["input"], int(posts["fresh_days"]), social.utcnow().date()) if posts else b["input"]
            items = run(accounts[best - 1], b["actor"], sent, int(b["max_items"]), cap)
            cost = getattr(items, "cost", None)
            SPEND[name] = cost
            if posts:
                found, dropped = social.posts(posts["kind"], items, fresh_days=int(posts["fresh_days"]), now=social.utcnow(), seen=seen)
                why = ", ".join(f"{reason} {count}" for reason, count in dropped.most_common())
                print(f"apify {name}: {len(items)} items, {len(found)} posts kept" + (f" (dropped: {why})" if why else ""))
            else:
                found = normalize(name, items, b.get("link_base", ""))
            hold(f"apify-{name}", every)
        except requests.RequestException as e:  # one board's failure leaves the others to run
            print(f"WARNING apify {name}: run failed ({type(e).__name__}), next run tries again")
            continue
        used, limit = month[best]
        print(f"apify {name}: cost " + (f"${cost:.4f}" if cost is not None else "unknown") + f" (cap ${cap:.2f}), account {best} had"
              f" ${used:.2f} of ${limit:.2f} used this month before it")
        if cost is not None and cost > cap * 1.05:
            print(f"WARNING apify {name}: cost ${cost:.4f} is over its cap ${cap:.2f}")
        # a run that gives nothing, or items none of which has a title and a link, is a board that stopped working
        # (LinkedIn Egypt's search gave 0 items, Seek's 50 items gave 0 postings on 2026-10-29)
        if not found:
            print(f"WARNING apify {name}: {len(items)} items, 0 postings: check its input or its fields")
        rows += found
    return rows
