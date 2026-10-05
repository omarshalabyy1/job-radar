r"""Write docs/companies.md from settings.yaml (companies), so the list in the docs is always the list
job-radar reads. Run it after you change a company:

    .venv\Scripts\python scripts\companies_doc.py

tests/test_limits.py fails while the two differ.
"""

import re
from pathlib import Path
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "companies.md"
# how job-radar reads a careers link, by its address (it confirms the platform on the first read)
PLATFORMS = [(r"greenhouse\.io", "Greenhouse, public API"), (r"lever\.co", "Lever, public API"),
             (r"ashbyhq\.com", "Ashby, public API"), (r"workable\.com", "Workable, public API"),
             (r"myworkdayjobs\.com", "Workday, through the 28,000-company feed"),
             (r"bamboohr\.com", "BambooHR, through the 28,000-company feed"),
             (r"successfactors|jobs\.sap\.com", "SuccessFactors search page"), (r"oraclecloud\.com", "Oracle Cloud page"),
             (r"taleo\.net", "Taleo page"), (r"eightfold\.ai", "Eightfold page")]


def read_as(link: str) -> str:
    return next((how for pattern, how in PLATFORMS if re.search(pattern, link)), "careers page (platform found on the first read)")


def render() -> str:
    companies = yaml.safe_load((ROOT / "settings.yaml").read_text(encoding="utf-8"))["companies"]
    rows = "\n".join(f"| {c['name']}{'' if c.get('starred', True) else ' (not starred)'} | "
                     f"[{urlsplit(c['link']).netloc}]({c['link']}) | {read_as(c['link'])} |" for c in companies)
    return f"""# Your companies

The {len(companies)} companies whose own career sites job-radar reads every day: only companies that hire in
Egypt, each through its jobs page filtered to Egypt where the site has a filter, and only their jobs
in Egypt are kept. Jobs at these companies are starred in your email, from any source.

This page is written from `settings.yaml` (companies) by `scripts/companies_doc.py`; change a company
there, then run the script. The tracker's Companies tab shows how each site was last read.

| Company | Egypt jobs page | Read as |
|---|---|---|
{rows}
"""


if __name__ == "__main__":
    DOC.write_text(render(), encoding="utf-8")
    print(f"wrote {DOC.relative_to(ROOT)}")
