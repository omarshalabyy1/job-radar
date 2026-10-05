r"""Open the career sites that show a bot check in a real Chromium window, so you can get past them
yourself; job-radar then reads them with your session. Run it on the laptop, not in Docker:

    .venv\Scripts\python scripts\open_blocked.py            # every site the runs marked blocked
    .venv\Scripts\python scripts\open_blocked.py <url> ...  # or these pages (a login you want kept)

For each site it opens the page and waits: pass the check, accept or refuse the cookies, log in if
you want to, then press Enter here. It saves the site's cookies and this browser's user agent to
output/sessions/<host>.json (gitignored, never committed): every source and the browser in Airflow
use them from the next run, which reads that company again at once (a hold on the site is lifted).
The window keeps its profile in output/browser-profile, so what you did is still there next time.
A session that expires shows the site as blocked again: run this again. The script itself never
solves anything, and never opens LinkedIn (its jobs come only from your alert emails).
"""

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

with psycopg.connect(host="localhost", port=5433, dbname="jobradar", user="jobradar",
                     password=os.environ["WAREHOUSE_PASSWORD"]) as conn:
    urls = sys.argv[1:] or [url for (url,) in conn.execute(
        "SELECT careers_url FROM core.company WHERE platform = 'blocked' ORDER BY company")]
    urls = [url for url in urls if "linkedin.com" not in url]
    if not urls:
        sys.exit("No site is blocked.")
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(ROOT / "output" / "browser-profile", headless=False)
        page = browser.pages[0]
        for url in urls:
            host = urlsplit(url).hostname
            try:
                page.goto(url, timeout=60000)
            except Exception as e:  # a site that drops the first try: reload it in the window
                print(f"{url} did not open ({e.__class__.__name__}): try reloading it in the window")
            input(f"{url}\n  Pass the check, accept the cookies or log in in the window, then press Enter here... ")
            session = {"user_agent": page.evaluate("navigator.userAgent"),
                       "cookies": [c for c in browser.cookies() if host.endswith(c["domain"].lstrip("."))]}
            (ROOT / "output" / "sessions").mkdir(parents=True, exist_ok=True)
            (ROOT / "output" / "sessions" / f"{host}.json").write_text(json.dumps(session), encoding="utf-8")
            (ROOT / "output" / "waits" / host).unlink(missing_ok=True)
            conn.execute("UPDATE core.company SET checked_at = NULL WHERE careers_url = %s", (url,))
            conn.commit()
            print(f"  saved output/sessions/{host}.json ({len(session['cookies'])} cookies)")
        browser.close()
