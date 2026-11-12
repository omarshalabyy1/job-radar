"""Jobs Claude scraped by hand for one site (skill scraping-orchestrator, "Jobs on request"), stored like any source's postings.

    python -m job_radar.manual_jobs SITE FILE.json     FILE.json: a list of {title, job_url, company, location, date_posted, description, searched_for}

Each posting is stored once (source manual:SITE, key its link), then transform and match_skills run so the jobs reach the tracker.
"""
from __future__ import annotations

import json
import sys

from .db import connect
from .sources.base import row


def add(conn, site: str, jobs: list[dict]) -> tuple[int, int]:
    """Store `jobs` under source manual:<site>; a job without a title or a link is no posting. Returns steps.load's (postings, dropped)."""
    from .steps import load  # steps imports the sources

    source = f"manual:{site}"
    rows = [row(source, j.get("searched_for"), j["title"], j.get("company"), j.get("location"), j["job_url"], j.get("date_posted"),
                j.get("description"), {**j, "scraped_by": "claude"}) for j in jobs if j.get("title") and j.get("job_url")]
    return load(conn, source, rows)


def main() -> None:
    from . import steps

    site, path = sys.argv[1:3]
    with connect() as conn:
        add(conn, site, json.load(open(path, encoding="utf-8")))
        steps.transform(conn)
        steps.match_skills(conn)


if __name__ == "__main__":
    main()
