"""The startup directory and the company boards it leads to, for the website.

    python -u scripts/find_boards.py load            # the startup lists into core.startup (one row per company domain)
    python -u scripts/find_boards.py lookup [limit]  # each startup's careers or home page, once: the board it names
    python -u scripts/find_boards.py employers       # the agencies' and outsourcers' boards (no startup row)

load reads, first list first (it keeps its values; a later list only fills empty ones, and turns the flags true):
your startups (output/startups/startups.csv, 'owner'), Y Combinator's active companies (yc-oss's public copy of the YC
directory, 'yc'), the YC 154 remote and visa sheets ('yc_visa', both versions merged), Wamda Capital's portfolio
('wamda') and the remote-hiring list (a PDF read with pdftotext, 'remote_list'). Flat6Labs publishes no list of its
startups' sites, so it is not read. The sheets and the PDF are Omar's downloads in E:\\GitHub\\references, read as data.

lookup reads a startup's pages (its careers page, its site, /careers, /jobs) with plain requests at each site's pace
(sources.base, never a browser), and adds the first company board they name (Greenhouse, Lever, Ashby, SmartRecruiters,
Workable, Recruitee, Personio) to core.board as a candidate (origin 'startup'), which extract_market then reads.
Priority, visa-friendly and hiring startups first. Each step is idempotent: re-running it changes nothing it already did."""

import csv
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_radar import db  # noqa: E402
from job_radar.config import OUTPUT  # noqa: E402
from job_radar.sources.base import ERRORS, Held, fetch, get  # noqa: E402
from job_radar.sources.market import boards_in  # noqa: E402

YC = "https://yc-oss.github.io/api/companies/all.json"
WAMDA = "https://wamdacapital.com/portfolio/"
REFERENCES = Path(r"E:\GitHub\references")
VISA_SHEETS = [REFERENCES / "yc-visa-sheet" / "YC_154_Global_Remote_Visa_Targets.xlsx", REFERENCES / "company-lists" / "sheet-1de03.xlsx"]
REMOTE_LIST = REFERENCES / "company-lists" / "remote-hiring-list.pdf"
NOT_COMPANIES = r"wamda|cloudflare|google|gstatic|gmpg|linkedin|twitter|facebook|instagram|youtube|stepconference|bldr\."
# agencies and outsourcers that hire remotely (Omar's list, 2026-10-17): their boards only, no startup row
EMPLOYERS = {"Randstad": "randstad.com", "Adecco": "adecco.com", "ManpowerGroup": "manpowergroup.com", "Hays": "hays.com",
             "Robert Walters": "robertwalters.com", "Michael Page": "michaelpage.com", "Robert Half": "roberthalf.com",
             "Antal": "antal.com", "Avomind": "avomind.com", "TaskUs": "taskus.com", "ModSquad": "modsquad.com",
             "Concentrix": "concentrix.com", "TDCX": "tdcx.com", "Sutherland": "sutherlandglobal.com",
             "Cognizant": "cognizant.com", "Genpact": "genpact.com", "Teleperformance": "tp.com"}

# the columns a list may give; the flags turn true when any list says so, the rest are kept from the first list
COLUMNS = ["company", "website", "one_liner", "industries", "batch", "location", "region", "team_size", "source", "source_id",
           "source_url", "careers_url", "hiring", "has_remote_roles", "open_source", "visa_friendly", "priority", "signal"]
FLAGS = ["has_remote_roles", "open_source", "visa_friendly", "priority"]


def domain(url: str | None) -> str | None:
    url = (url or "").strip()
    host = (urlsplit(url if "//" in url else f"https://{url}").hostname or "").lower().removeprefix("www.")
    return host if "." in host and " " not in host else None


def owner() -> list[dict]:
    path = OUTPUT / "startups" / "startups.csv"
    rows = []
    for r in csv.DictReader(path.open(encoding="utf-8")) if path.exists() else []:
        size = re.sub(r"\D", "", r.get("size") or "")
        rows.append({"company": r["name"], "website": r.get("website") or None, "careers_url": r.get("careers") or None,
                     "location": r.get("city") or None, "region": r.get("region") or None, "one_liner": r.get("what") or None,
                     "team_size": int(size) if size else None, "source": "owner"})
    return rows


def yc() -> list[dict]:
    return [{"company": c["name"], "website": c.get("website"), "one_liner": c.get("one_liner"),
             "industries": c.get("industries") or [], "batch": c.get("batch"), "location": c.get("all_locations"),
             "team_size": c.get("team_size") or None, "source": "yc", "source_id": str(c["id"]), "source_url": c.get("url"),
             "hiring": bool(c.get("isHiring")), "has_remote_roles": "Remote" in (c.get("regions") or []),
             "open_source": "Open Source" in (c.get("tags") or [])}
            for c in get(YC).json() if c.get("status") == "Active"]


def yc_visa() -> list[dict]:
    """Both versions of the YC 154 sheet: 'YC 154 Targets' (its header is the row starting 'Company') and 'Priority
    Shortlist'. A strong remote signal is remote; a visa signal that is not 'No blanket sponsorship claim' is visa-friendly."""
    import openpyxl
    rows, shortlist = {}, set()
    for path in [p for p in VISA_SHEETS if p.exists()]:
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for sheet in ("YC 154 Targets", "Priority Shortlist"):
            table = [r for r in book[sheet].iter_rows(values_only=True) if r and r[0]]
            head = next(i for i, r in enumerate(table) if r[0] == "Company")
            names = [str(h or "") for h in table[head]]
            for r in table[head + 1:]:
                f = dict(zip(names, r))
                if sheet == "Priority Shortlist":
                    shortlist.add(str(f["Company"]).strip().lower())
                    continue
                remote, visa = f.get("Remote Signal") or "", f.get("Visa / Relocation Signal") or ""
                hiring = f.get("YC Jobs / Hiring Portal") or f.get("YC Hiring Portal") or ""
                rows.setdefault(str(f["Company"]).strip(), {
                    "company": str(f["Company"]).strip(), "website": f.get("Official Website"),
                    "industries": [f["Sector"]] if f.get("Sector") else [], "source": "yc_visa",
                    "source_url": f.get("YC Profile"), "careers_url": hiring if "/companies/" in hiring else None,
                    "has_remote_roles": remote.lower().startswith("strong"),
                    "visa_friendly": bool(visa) and not visa.lower().startswith("no blanket"),
                    "signal": " | ".join(filter(None, [remote, visa, f.get("Visa / Remote Notes"),
                                                       f"Target roles: {f['Target Roles']}" if f.get("Target Roles") else None]))[:2000]})
    for r in rows.values():
        r["priority"] = r["company"].lower() in shortlist
    return list(rows.values())


def wamda() -> list[dict]:
    return [{"company": d.split(".")[0].title(), "website": url, "source": "wamda", "source_url": WAMDA}
            for url in dict.fromkeys(re.findall(r'href="(https?://[^"/]+)', get(WAMDA).text))
            if (d := domain(url)) and not re.search(NOT_COMPANIES, d)]


def remote_list() -> list[dict]:
    """The remote-hiring PDF: a 'Name  https://site  Region' table; the region, when on the same line."""
    if not (REMOTE_LIST.exists() and shutil.which("pdftotext")):
        print("remote_list: no PDF or no pdftotext, skipped")
        return []
    text = subprocess.run(["pdftotext", "-layout", str(REMOTE_LIST), "-"], capture_output=True, text=True, encoding="utf-8").stdout
    rows = []
    for line in text.splitlines():
        if m := re.match(r"^(\S.*?)\s{2,}(https?://\S+)(?:\s{2,}(.+))?$", line.strip()):
            rows.append({"company": m.group(1).strip(), "website": m.group(2), "region": (m.group(3) or "").strip() or None,
                         "source": "remote_list", "has_remote_roles": True})
    return rows


def upsert(conn, rows: list[dict]) -> int:
    """Each row by its domain: a new one is inserted; an existing one keeps its values and takes only the empty ones and
    the flags (YC's directory sets the hiring flag as it now stands). Only a row that changes is written (updated_at)."""
    keep = [c for c in COLUMNS if c not in FLAGS and c not in ("company", "source", "hiring", "industries")]
    sets = ([f"{c} = coalesce(s.{c}, EXCLUDED.{c})" for c in keep] + [f"{c} = s.{c} OR EXCLUDED.{c}" for c in FLAGS] + [
        "industries = CASE WHEN s.industries = '{}' THEN EXCLUDED.industries ELSE s.industries END",
        "hiring = CASE WHEN EXCLUDED.source = 'yc' THEN EXCLUDED.hiring ELSE s.hiring OR EXCLUDED.hiring END"])
    old = [f"s.{s.split(' = ', 1)[0]}" for s in sets]
    new = [s.split(" = ", 1)[1] for s in sets]
    sql = (f"INSERT INTO core.startup AS s (domain, {', '.join(COLUMNS)}) VALUES (%(domain)s, {', '.join(f'%({c})s' for c in COLUMNS)})"
           f" ON CONFLICT (domain) DO UPDATE SET {', '.join(sets)}, updated_at = now()"
           f" WHERE ({', '.join(old)}) IS DISTINCT FROM ({', '.join(new)})")
    clean = {}
    for r in rows:  # the first row of a domain in this list
        if (d := domain(r.get("website") or r.get("careers_url"))) and d not in clean:
            clean[d] = {c: r.get(c) for c in COLUMNS} | {"domain": d, "industries": r.get("industries") or []} | {
                f: bool(r.get(f)) for f in [*FLAGS, "hiring"]}
    with conn.cursor() as cur:
        cur.executemany(sql, list(clean.values()))
        changed = cur.rowcount
    conn.commit()
    return changed


def load(conn) -> None:
    for name in (owner, yc, yc_visa, wamda, remote_list):
        rows = name()
        print(f"{name.__name__}: {len(rows)} startups read, {upsert(conn, rows)} rows added or changed")
    print(conn.execute("SELECT count(*), count(*) FILTER (WHERE visa_friendly), count(*) FILTER (WHERE priority),"
                       " count(*) FILTER (WHERE has_remote_roles) FROM core.startup").fetchone(), "(startups, visa, priority, remote)")


def boards_of(url: str) -> list[tuple[str, str]]:
    """The boards a company's pages name: the page given, then its /careers and /jobs pages."""
    url = url if "//" in url else f"https://{url}"
    base = f"{urlsplit(url).scheme or 'https'}://{urlsplit(url).netloc}"
    for page in dict.fromkeys([url, f"{base}/careers", f"{base}/jobs"]):
        try:
            r = fetch("GET", page, hold_on_403=False, timeout=20)
        except (*ERRORS, Held, ValueError, TypeError):  # a site down, or an address that is not one
            continue
        if found := boards_in(f"{r.url} {r.text}"):
            return found
    return []


def lookup(conn, limit: int) -> None:
    todo = conn.execute(
        "SELECT domain, company, coalesce(careers_url, website, domain) FROM core.startup WHERE looked_up_at IS NULL"
        " ORDER BY priority DESC, visa_friendly DESC, source = 'yc_visa' DESC, hiring DESC, source = 'owner' DESC, domain"
        " LIMIT %s", (limit,)).fetchall()
    conn.commit()
    added = 0
    for site, company, url in todo:
        found = boards_of(url)
        if found:
            ats, token = found[0]
            added += conn.execute("INSERT INTO core.board (ats, token, company, domain, origin) VALUES (%s, %s, %s, %s, 'startup')"
                                  " ON CONFLICT DO NOTHING", (ats, token, company, site)).rowcount
        conn.execute("UPDATE core.startup SET board = %s, looked_up_at = now() WHERE domain = %s",
                     (":".join(found[0]) if found else None, site))
        conn.commit()  # each startup saved as it is looked up: a run stopped part way keeps what it found
        print(f"{site}: {':'.join(found[0]) if found else 'no board'}", flush=True)
    print(f"lookup: {len(todo)} startups looked up, {added} boards added")


def employers(conn) -> None:
    for company, site in EMPLOYERS.items():
        found = boards_of(f"https://{site}/careers")
        if found:
            conn.execute("INSERT INTO core.board (ats, token, company, domain, origin) VALUES (%s, %s, %s, %s, 'employer')"
                         " ON CONFLICT DO NOTHING", (*found[0], company, site))
            conn.commit()
        print(f"{company} ({site}): {':'.join(found[0]) if found else 'no board this script reads (Workday, iCIMS ... or none)'}",
              flush=True)


if __name__ == "__main__":
    step, args = (sys.argv[1:] or ["load"])[0], sys.argv[2:]
    with db.connect() as connection:
        {"load": lambda: load(connection), "lookup": lambda: lookup(connection, int(args[0]) if args else 100),
         "employers": lambda: employers(connection)}[step]()
