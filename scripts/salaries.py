r"""What the jobs you are applying to pay, from Glassdoor: for each job, the company's own salaries
for that role in that country, and the role's market range there (a job with no country is skipped:
Glassdoor would answer for the viewer's own country). Run it on the laptop, not in Docker:

    python scripts\salaries.py                  # every job you saved in the tracker
    python scripts\salaries.py 18135 18394      # or these job ids
    python scripts\salaries.py 18394=CIB        # a job with its company's name on Glassdoor
                                                # (when the radar has it in Arabic, say)
    python scripts\salaries.py --all            # every company, country and role in the radar's jobs:
                                                # stops after 12 hours, skips what an earlier run read

It uses Playwright's Chromium (playwright install chromium) in a real window placed off-screen:
Glassdoor's bot check stops every headless mode and passes only the first page of a browser session,
so each page opens in a fresh context (tested 2026-10-07). An unpassed check counts as a 429. No
login, no CAPTCHA solving. Glassdoor answered 429 at 7 pages at once, so this reads one page at a
time and, on a 429, waits and tries the page again.

Rows go to output/salaries/<date>.jsonl (gitignored) and are printed per job. Figures are EGP or the
country's currency a month, as Glassdoor shows them. The salary you ask for is still your own rule
(output/applications/application-answers.md); this is the evidence behind it.
"""

import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import quote

import psycopg
from dotenv import load_dotenv
from playwright.sync_api import Error as PlaywrightError, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
GD = "https://www.glassdoor.com"

# Glassdoor's country ids, each checked on its role page ("How much does a ... make in <country>?")
COUNTRIES = {"Egypt": 69, "USA": 1, "United States": 1, "United Kingdom": 2, "UK": 2, "Canada": 3,
             "UAE": 6, "United Arab Emirates": 6, "Germany": 96, "Saudi Arabia": 207, "Spain": 219,
             "Netherlands": 178}
SLUG = {1: "United-States", 2: "United-Kingdom", 3: "Canada", 6: "United-Arab-Emirates", 69: "Egypt",
        96: "Germany", 207: "Saudi-Arabia", 219: "Spain", 178: "Netherlands"}

# the Glassdoor title searched for each job-radar role, and the words that mark the same role
ROLES = {"Data Engineer": ("data engineer", r"data engineer"),
         "AI & Data Engineer": ("data engineer", r"data engineer|ai engineer"),
         "AI Engineer (NLP, LLM, agents, RAG)": ("ai engineer", r"\bai engineer|machine learning|\bml engineer"),
         "BI Developer": ("business intelligence developer", r"\bbi\b|business intelligence|power bi"),
         "Data Analyst": ("data analyst", r"data analyst|analytics")}
SCIENTIST = ("data scientist", r"data scien")

MONEY = r"([A-Z]{3}|[$£€])\s?([\d,.]+K?)"  # the currency, then the figure
HEAD = re.compile(rf"Total pay range\s+{MONEY}\s*-\s*{MONEY}\s*/(mo|yr)\s+{MONEY}\s*/(?:mo|yr)\s+Median total pay")
LISTED = re.compile(rf"([A-Z][\w&/,()\- ]{{2,60}}?)\s+([\d,]+) Salaries submitted\s+{MONEY}\s*-\s*{MONEY}\s*/(mo|yr)")


def amount(text: str) -> int:
    text = text.replace(",", "")
    return round(float(text[:-1]) * 1000) if text.endswith("K") else round(float(text))


def flat_text(page) -> str:
    """The page's text on one line; Glassdoor splits a figure over several tags ("EGP 1" "3K")."""
    text = re.sub(r"\s+", " ", page.inner_text("body"))
    return re.sub(r"(\d) (?=\d|K\b)", r"\1", text)


class Throttled(Exception):
    """Glassdoor still answers 429 or its bot check after four tries: the run stops, to go on later."""


def read(session, url: str):
    """One page, in a fresh browser context: Glassdoor lets the first page of a session through and
    challenges the next ones (tested 2026-10-07). On a 429 or an unpassed check, wait a minute (then
    two, three) and ask again."""
    for attempt in range(4):
        for context in session.contexts:
            context.close()
        page, status = session.new_page(), None
        try:  # Cloudflare's "Just a moment..." JavaScript check passes on its own in a real window
            status = page.goto(url, wait_until="domcontentloaded", timeout=60000).status
            page.wait_for_function("document.title !== 'Just a moment...'", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=10000)
        except PlaywrightError:  # a check that never clears, or its reload cutting into ours
            pass
        if status not in (None, 429) and page.title() != "Just a moment...":
            return page
        print(f"  {status} or bot check from Glassdoor, waiting {60 * (attempt + 1)} s")
        time.sleep(60 * (attempt + 1))
    raise Throttled(url)


def market(session, title: str, country: int) -> dict | None:
    """The role's pay range and median in the country."""
    slug, place = title.replace(" ", "-"), SLUG[country].lower()
    url = (f"{GD}/Salaries/{place}-{slug}-salary-SRCH_IL.0,{len(place)}_IN{country}"
           f"_KO{len(place) + 1},{len(place) + 1 + len(slug)}.htm")
    m = HEAD.search(flat_text(read(session, url)))
    return m and {"currency": m[1], "low": amount(m[2]), "high": amount(m[4]), "per": m[5],
                  "median": amount(m[7]), "url": url}


def employer(session, company: str) -> tuple[str, str] | None:
    """The company's Glassdoor slug and id: the first company Glassdoor's search finds whose name
    starts with the name searched for ("CIB" finds CIBC first, which is not it)."""
    page = read(session, f"{GD}/Search/results.htm?keyword={quote(company)}")
    wanted = re.sub(r"\W+", "-", company.strip()).lower()
    for href in page.eval_on_selector_all("a[href]", "links => links.map(a => a.getAttribute('href'))"):
        m = re.search(r"/Overview/Working-at-(.+?)-EI_IE(\d+)\.", href)
        if m and (m[1].lower() == wanted or m[1].lower().startswith(wanted + "-")):
            return m[1], m[2]
    return None


def company_salaries(session, slug: str, eid: str, country: int) -> list[dict]:
    """All the company's salaries in the country, reading its pages until one adds nothing new."""
    place = SLUG[country]
    base = (f"{GD}/Salary/{slug}-{place}-Salaries-EI_IE{eid}.0,{len(slug)}"
            f"_IL.{len(slug) + 1},{len(slug) + 1 + len(place)}_IN{country}")
    found, seen = [], set()
    for n in range(1, 21):
        text = flat_text(read(session, f"{base}.htm" if n == 1 else f"{base}_IP{n}.htm"))
        rows = [(re.sub(r"(?i)^sort by most salaries ", "", m[1]).strip(), int(m[2].replace(",", "")),
                 m[3], amount(m[4]), amount(m[6]), m[7]) for m in LISTED.finditer(text)]
        new = [r for r in rows if r not in seen]
        if not new:
            break
        seen.update(new)
        found += [{"title": t, "reports": k, "currency": cur, "low": lo, "high": hi, "per": per}
                  for t, k, cur, lo, hi, per in new]
    return found


def country_of(place: str, location: str) -> int | None:
    if place in COUNTRIES:
        return COUNTRIES[place]
    return next((cid for name, cid in COUNTRIES.items() if name.lower() in location.lower()), None)


def lookup(session, markets, employers, companies, search, pattern, country, where, name) -> list[dict]:
    """The market row and the company's rows for one company, country and role; each page is read
    once per run (the dicts)."""
    rows = []
    if (search, country) not in markets:
        markets[search, country] = market(session, search, country)
    if m := markets[search, country]:
        rows.append({"source": "market", "title": search, **m})
    if name not in employers:
        employers[name] = employer(session, name)
    if found := employers[name]:
        if (found, country) not in companies:
            companies[found, country] = company_salaries(session, *found, country)
        own = [r for r in companies[found, country] if re.search(pattern, r["title"], re.I)]
        rows += [{"source": "company", **r} for r in own]
        if not own:
            print(f"  {found[0]} on Glassdoor: no salaries for this role in {where}")
    else:
        print("  the company is not on Glassdoor")
    return rows


def done_before(folder: Path) -> set:
    """(company, country, role) already read by an earlier run, found or not."""
    return {(r["company"], r["country"], r["role"]) for path in folder.glob("*.jsonl")
            for r in map(json.loads, path.read_text(encoding="utf-8").splitlines())}


def main() -> None:
    every = "--all" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--all"]
    with psycopg.connect(host="localhost", port=5433, dbname="jobradar", user="jobradar",
                         password=os.environ["WAREHOUSE_PASSWORD"]) as conn:
        names = dict(a.split("=", 1) for a in args if "=" in a)  # job id -> Glassdoor name
        if every:  # one job for each company, country and role
            jobs = conn.execute("SELECT DISTINCT ON (company, place, role) job_id, title, company, role, place,"
                                " location FROM mart.job_status WHERE company <> ''"
                                " ORDER BY company, place, role, job_id").fetchall()
        else:
            ids = [int(a.split("=")[0]) for a in args] or [i for (i,) in conn.execute(
                "SELECT job_id FROM core.application WHERE status = 'saved' ORDER BY job_id")]
            jobs = conn.execute("SELECT job_id, title, company, role, place, location FROM mart.job_status"
                                " WHERE job_id = ANY(%s) ORDER BY job_id", (ids,)).fetchall()
    out = ROOT / "output" / "salaries" / f"{date.today()}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    skip = done_before(out.parent) if every else set()
    stop = time.monotonic() + 12 * 3600
    markets, employers, companies = {}, {}, {}  # each page read once per run
    with sync_playwright() as p, open(out, "a", encoding="utf-8") as f:
        session = p.chromium.launch(headless=False, args=["--window-position=-32000,-32000"])
        for job_id, title, company, role, place, location in jobs:
            search, pattern = SCIENTIST if "scien" in title.lower() else ROLES.get(role, ROLES["Data Engineer"])
            country = country_of(place or "", location or "")
            where = SLUG.get(country, "worldwide")
            if (company, where, role) in skip:
                continue
            if time.monotonic() > stop:
                print("\n12 hours are up: run it again to go on where it stopped")
                break
            print(f"\n#{job_id} {title} @ {company} ({where}) - Glassdoor '{search}'", flush=True)
            if not country:  # Glassdoor would answer for the viewer's own country (Egypt), not the job's
                print("  no country to look up: the role figure applies")
                f.write(json.dumps({"job_id": job_id, "company": company, "country": where, "role": role,
                                    "source": "none"}, ensure_ascii=False) + "\n")
                continue
            try:
                rows = lookup(session, markets, employers, companies, search, pattern, country, where,
                              names.get(str(job_id), company))
            except Throttled:
                print("\nGlassdoor still answers 429 or its bot check after four tries: stopped. Run it again later to go on.")
                break
            for r in rows:
                median = f", median {r['median']:,}" if r.get("median") else ""
                reports = f" ({r['reports']} reports)" if r.get("reports") else ""
                print(f"  {r['source']:8} {r['title']}{reports}: {r['currency']} {r['low']:,} - {r['high']:,}"
                      f" /{r['per']}{median}")
                f.write(json.dumps({"job_id": job_id, "company": company, "country": where, "role": role,
                                    **r}, ensure_ascii=False) + "\n")
            if not rows:
                print("  no salary found")
                f.write(json.dumps({"job_id": job_id, "company": company, "country": where, "role": role,
                                    "source": "none"}, ensure_ascii=False) + "\n")
            f.flush()
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()
