"""Are we using every board, portal, company and startup? Read-only: for the last 7 days, what each source gave (postings, distinct
companies, jobs of yours), which tracked companies and startups gave a job, and which gave none.

    python scripts/audit_apps.py      prints the report and writes output/reviews/app-audit-<day>.md
"""
import csv
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
from job_radar.config import OUTPUT  # noqa: E402
from job_radar.db import connect  # noqa: E402

GROUPS = {
    "Egypt boards": {"wuzzuf", "tanqeeb"},
    "Gulf boards": {"bayt", "naukrigulf", "gulftalent", "dubizzle"},
    "Global boards": {"indeed", "jooble", "freehire", "workable"},
    "Remote boards": {"himalayas", "weworkremotely", "relomote", "remotive", "remoteok", "jobicy", "workingnomads", "arbeitnow",
                      "dailyremote", "remoteco", "inclusivelyremote", "hireeing"},
    "Startup boards": {"welcometothejungle", "startup_jobs", "builtin", "ycombinator", "wellfound"},
}


def group_of(source: str) -> str:
    """The group a raw source belongs to; a source that is none of these is a company's own career page or ATS board."""
    if source.startswith("apify:") or source == "linkedin":
        return "LinkedIn and Apify"
    if source == "email":
        return "Email alerts"
    if source.startswith("manual:"):
        return "Manual scrapes"
    return next((name for name, sources in GROUPS.items() if source in sources), "Company career pages")


def verdict(postings: int, yours: int) -> str:
    return "NO POSTINGS in 7 days" if not postings else "working" if yours else "feeds the website only"


def main() -> None:
    out = [f"# App audit {date.today()}: last 7 days, from the warehouse", ""]
    with connect() as conn:
        raw = {s: (n, c, last) for s, n, c, last in conn.execute(
            "SELECT source, count(*), count(DISTINCT company), max(last_seen) FROM raw.job_posting WHERE last_seen >= current_date - 7 GROUP BY 1")}
        yours = dict(conn.execute("SELECT source, count(*) FROM core.job WHERE in_scope AND first_seen >= current_date - 7 GROUP BY 1").fetchall())
        counted = {b for (b,) in conn.execute("SELECT DISTINCT board FROM core.extract_count WHERE day >= current_date - 7")}
        expected = {s for names in GROUPS.values() for s in names}
        by_group = defaultdict(list)
        for source in sorted(set(raw) | expected | counted):
            n, c, last = raw.get(source, (0, 0, None))
            by_group[group_of(source)].append((source, n, c, yours.get(source, 0), last))
        for group in [*GROUPS, "LinkedIn and Apify", "Email alerts", "Manual scrapes", "Company career pages"]:
            rows = by_group.get(group, [])
            if group == "Company career pages":  # hundreds of sources: totals, then the biggest
                rows.sort(key=lambda r: -r[1])
                out += [f"## {group}: {len(rows)} sources, {sum(r[1] for r in rows)} postings, {sum(r[3] for r in rows)} yours", ""]
                rows = rows[:12]
            else:
                out += [f"## {group}", ""]
            out += ["| Source | Postings | Companies | Yours | Last seen | Verdict |", "|---|---|---|---|---|---|"]
            out += [f"| {s} | {n} | {c} | {y} | {last or '-'} | {verdict(n, y)} |" for s, n, c, y, last in rows]
            out.append("")
        # tracked companies (core.company): did a posting of that company come in this week?
        names = {c: p for c, p in conn.execute("SELECT company, platform FROM core.company")}
        seen = {c.lower() for (c,) in conn.execute("SELECT DISTINCT company FROM raw.job_posting WHERE last_seen >= current_date - 7")}
        def gave(name: str) -> bool:
            low = name.lower()
            return any(low in s or s in low for s in seen if len(s) > 3)
        by_platform = defaultdict(lambda: [0, 0, []])
        for name, platform in names.items():
            row = by_platform[platform or "not detected"]
            row[0] += 1
            row[1] += gave(name)
            if not gave(name):
                row[2].append(name)
        out += [f"## Tracked companies: {len(names)}, {sum(r[1] for r in by_platform.values())} gave a posting this week", "",
                "| Platform | Companies | Gave a job | Gave none |", "|---|---|---|---|"]
        out += [f"| {p} | {n} | {g} | {n - g} |" for p, (n, g, _) in sorted(by_platform.items(), key=lambda kv: -kv[1][0])]
        out += ["", "Gave none (first 25): " + ", ".join(sorted(n for r in by_platform.values() for n in r[2])[:25]), ""]
        boards = conn.execute("SELECT status, count(*), coalesce(sum(jobs), 0) FROM core.board GROUP BY 1 ORDER BY 2 DESC").fetchall()
        out += ["## ATS boards found by the market harvest (core.board)", "", "| Status | Boards | Jobs |", "|---|---|---|"]
        out += [f"| {s} | {n} | {j} |" for s, n, j in boards] + [""]
    startups = list(csv.DictReader((OUTPUT / "startups" / "startups.csv").open(encoding="utf-8")))
    with_careers = [s for s in startups if s["careers"]]
    gave_job = [s for s in startups if gave(s["name"])]
    out += [f"## Startups (output/startups/startups.csv): {len(startups)} listed, {len(with_careers)} with a careers page, {len(gave_job)} gave a posting this week", ""]
    report = "\n".join(out)
    folder = OUTPUT / "reviews"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"app-audit-{date.today()}.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
