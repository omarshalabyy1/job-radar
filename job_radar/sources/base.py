"""What every source shares: the time budget, the row shape, and the one door every request goes
through - holds, the pace per site, the real-Chrome retry, saved sessions, the headless browser."""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import requests
from curl_cffi import requests as chrome_requests

from ..config import HOURS_OLD, ROOT


STARTED = time.monotonic()  # each step is its own process: its time budget counts from here


def time_left(budget: float) -> float:
    return STARTED + budget - time.monotonic()


def since() -> str:
    """The oldest posting date in the window, as YYYY-MM-DD."""
    return str(date.today() - timedelta(hours=HOURS_OLD))


def week() -> str:
    """The oldest posting date for the boards whose dates run days late, as YYYY-MM-DD: a job already
    stored is not stored twice, so the longer look back only catches what they publish late."""
    return str(date.today() - timedelta(days=7))


def row(source, searched_for, title, company, location, job_url, date_posted, description, payload) -> dict:
    return {"source": source, "searched_for": searched_for, "title": title, "company": company or "",
            "location": location or "", "job_url": job_url, "date_posted": date_posted,
            "description": description, "payload": payload}


BROWSERS = threading.local()  # one headless Chromium per worker thread, see rendered()

BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/129.0 Safari/537.36"}


# Every request a source makes goes through fetch(). A host that answers 429 Too Many Requests (or 503
# with a Retry-After) is asked nothing more, by any step or run, until the time it gives (an hour
# when it gives none); one that still answers 401 or 403 to a real Chrome's handshake, for 6 hours;
# a host can also be held on our side (Workable: one round a day). One file per host in output/waits/
# holds the time, as the extract steps are separate processes side by side.
WAITS = ROOT / "output" / "waits"

# Seconds between two requests to one host, across a step's threads (request()): 1, unless named here.
# Workable's search is limited by the day, not the second (one round a day); the 28,000-company feed
# is files on GitHub's servers.
GAP = {"jobs.workable.com": 0.25, "feashliaa.github.io": 0.0}

NEXT_TURN: dict[str, float] = {}

TURNS = threading.Lock()

CHROME_ONLY: set[str] = set()  # hosts that answered only curl_cffi in this step (NaukriGulf hangs plain requests 15 s)


class Held(Exception):
    """A host not to be asked yet."""


class Challenge(Exception):
    """A page that shows a bot check, or a site that drops the headless browser."""


def waiting(host: str) -> float:
    """Seconds left before `host` may be asked again; 0 when it may be asked now."""
    try:
        return max(0.0, float((WAITS / host).read_text()) - time.time())
    except (OSError, ValueError):
        return 0.0


def hold(host: str, seconds: float) -> None:
    """Ask `host` nothing for `seconds`, in every step and run."""
    WAITS.mkdir(parents=True, exist_ok=True)
    (WAITS / host).write_text(str(time.time() + seconds))


def retry_after(value: str | None) -> float:
    """A Retry-After header in seconds (it gives seconds or a date); an hour when there is none."""
    if value and value.strip().isdigit():
        return float(value)
    try:
        return max(60.0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return 3600.0


def duration(seconds: float) -> str:
    return f"{seconds / 60:.0f} minutes" if seconds < 3600 else f"{seconds / 3600:.1f} hours"


# A site you opened by hand (scripts/open_blocked.py): its cookies and that browser's user agent
SESSIONS = ROOT / "output" / "sessions"

# A request that failed, from either client
ERRORS = (requests.RequestException, chrome_requests.exceptions.RequestException)

# A bot check's own page, not the passive Cloudflare script many ordinary pages carry
CHALLENGE = r"<title>(Just a moment|Access Denied|Attention Required)|cf-chl-"


def changed(source: str, what: str) -> None:
    """A page or API that gave no items at all, before any filter: most likely the site changed (its
    layout, its API), not a quiet day. A warning in the task's log, for /job-radar to report."""
    print(f"WARNING {source}: {what} - has the site changed?")


def session(host: str) -> dict | None:
    """The session saved for `host` (user_agent, cookies), or None."""
    try:
        return json.loads((SESSIONS / f"{host}.json").read_text())
    except (OSError, ValueError):
        return None


def pace(host: str) -> None:
    """Wait for `host`'s turn: GAP seconds after the request before it, whichever thread made it."""
    with TURNS:
        turn = max(time.monotonic(), NEXT_TURN.get(host, 0.0))
        NEXT_TURN[host] = turn + GAP.get(host, 1.0)
    while (left := turn - time.monotonic()) > 0:  # Windows' sleep can wake a few milliseconds early
        time.sleep(left)


def request(method: str, url: str, **kwargs):
    """One request, as a browser makes it, at the host's pace: with requests first, and when the site
    turns it away (401, 403), drops the connection or never answers, once more with curl_cffi, whose
    TLS handshake is a real Chrome's - the check many bot filters make (it opened 5 of the 6 career
    sites that refused requests, 2026-10-05; Bain is the other way round, hence requests first). Both
    tries together keep to the caller's timeout: requests gets half, curl_cffi what is left. A site
    you opened by hand gets your session's cookies and user agent."""
    host = urlsplit(url).hostname
    pace(host)
    headers = {**BROWSER, **kwargs.pop("headers", {})}
    if saved := session(host):
        headers["User-Agent"] = saved["user_agent"]
        kwargs["cookies"] = {c["name"]: c["value"] for c in saved["cookies"]}
    timeout, started = kwargs.pop("timeout", 60), time.monotonic()
    if host not in CHROME_ONLY:
        try:
            r = requests.request(method, url, headers=headers, timeout=timeout / 2, **kwargs)
            if r.status_code not in (401, 403):
                return r
        except (requests.Timeout, requests.ConnectionError):  # ConnectionError covers an SSL handshake refused
            pass
        pace(host)
    if not saved:  # curl_cffi sends the user agent of the Chrome it imitates
        del headers["User-Agent"]
    r = chrome_requests.request(method, url, headers=headers, impersonate="chrome",
                                timeout=max(1.0, timeout - (time.monotonic() - started)), **kwargs)
    if r.status_code < 400:  # only Chrome gets through: this step asks it as Chrome from now on
        CHROME_ONLY.add(host)
    return r


def fetch(method: str, url: str, hold_on_403: bool = True, **kwargs):
    """hold_on_403=False for a single job page (describe): its host can be a platform's API too
    (apply.workable.com), which one refused page must not stop for 6 hours."""
    host = urlsplit(url).hostname
    if left := waiting(host):
        raise Held(f"{host} is held for {duration(left)} more")
    r = request(method, url, **kwargs)
    if r.status_code == 429 or (r.status_code == 503 and "Retry-After" in r.headers):
        hold(host, retry_after(r.headers.get("Retry-After")))
        raise Held(f"{host} answered {r.status_code}: held for {duration(waiting(host))}")
    if r.status_code in (401, 403):  # turned away even as a real Chrome: asking again soon risks a ban
        if hold_on_403:
            hold(host, 6 * 3600)
        raise Held(f"{host} answered {r.status_code}" + (": held for 6 hours" if hold_on_403 else ""))
    r.raise_for_status()
    return r


def get(url: str, **params):
    return fetch("GET", url, params=params)


def relative_date(text: str) -> date | None:
    """'6 hours ago', '2 days ago', 'yesterday' or '4 August 2026' as a date; None when unclear."""
    text = text.strip().lower()
    if m := re.match(r"(\d+)\s+(minute|hour|day)s?\s+ago", text):
        return date.today() - timedelta(days=int(m.group(1)) if m.group(2) == "day" else 0)
    if text == "yesterday":
        return date.today() - timedelta(days=1)
    try:
        return datetime.strptime(text, "%d %B %Y").date()
    except ValueError:
        return None


def rendered(url: str) -> str:
    """The page's HTML after its JavaScript ran, in a headless Chromium (Playwright): for career
    pages that build their job list in the browser. Each worker thread starts one Chromium and
    reuses it for every page (Playwright is one instance per thread); it ends with the step. The
    page is read once it has loaded and gone quiet, or 10 seconds after it loaded: busy sites (IBM,
    Bain, Capgemini) never go quiet, and waiting for it timed them out. A site you opened by hand
    gets your session; a page that still shows a bot check raises, for you to open."""
    from playwright.sync_api import Error as PageError, TimeoutError as PageTimeout, sync_playwright
    if not hasattr(BROWSERS, "chromium"):
        BROWSERS.chromium = sync_playwright().start().chromium.launch()
    saved = session(urlsplit(url).hostname)
    context = BROWSERS.chromium.new_context(
        user_agent=saved["user_agent"] if saved else BROWSER["User-Agent"],
        storage_state={"cookies": saved["cookies"], "origins": []} if saved else None)
    try:
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except PageError as e:
            if "ERR_HTTP2_PROTOCOL_ERROR" in str(e):  # how McKinsey's site (Akamai) drops headless Chromium
                raise Challenge("it drops headless Chromium (ERR_HTTP2_PROTOCOL_ERROR)") from e
            raise
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except PageTimeout:  # still busy: what has loaded is read
            pass
        html = page.content()
    finally:
        context.close()
    if re.search(CHALLENGE, html[:20000]):
        raise Challenge("its page shows a bot check")
    return html
