"""Each reader against a saved sample of its site (tests/fixtures/), without the network: a site that
changes its layout or API fails here first. Run on the laptop: .venv\\Scripts\\python -m pytest tests
"""

import importlib.util
import json
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

from job_radar.sources import egypt, gulf, remote, startups

FIXTURES = Path(__file__).parent / "fixtures"


class Saved:
    """A saved response, with the parts of one the readers use."""
    def __init__(self, name_or_text: str):
        path = FIXTURES / name_or_text
        self.text = path.read_text(encoding="utf-8") if path.exists() else name_or_text
        self.content = self.text.encode()

    def json(self):
        return json.loads(self.text)


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    for module in (egypt, gulf, remote, startups):  # the saved samples are from 2026-10: every date is in the window
        for window in ("since", "week"):
            if hasattr(module, window):
                monkeypatch.setattr(module, window, lambda: "2000-01-01")


def titles(rows):
    return sorted(r["title"] for r in rows)


def test_wuzzuf_reads_its_api_newest_first(monkeypatch):
    def fetch(method, url, **kw):
        if url.endswith("/search/job"):
            return Saved("wuzzuf_search.json") if json.loads(kw["data"])["startIndex"] == 0 else Saved('{"data": []}')
        return Saved("wuzzuf_jobs.json")
    monkeypatch.setattr(egypt, "fetch", fetch)
    rows = egypt.wuzzuf()
    # the 1 October job is more than a day older than the newest (5 October): outside the window
    assert titles(rows) == ["Data Engineer"] and rows[0]["company"] == "Acme Egypt"
    assert rows[0]["job_url"] == "https://wuzzuf.net/jobs/p/a1" and rows[0]["location"] == "Cairo, Egypt"


def test_tanqeeb_reads_its_cards(monkeypatch):
    monkeypatch.setattr(egypt, "TANQEEB_SITES", {"egypt": "Egypt"})
    monkeypatch.setattr(egypt, "TANQEEB_PAGES", ["it-jobs"])
    monkeypatch.setattr(egypt, "since", lambda: "2026-01-01")  # the 2020 card is old
    monkeypatch.setattr(egypt, "get", lambda url, **params: Saved("tanqeeb.html"))
    rows = egypt.tanqeeb()
    assert titles(rows) == ["Data Engineer"]
    assert rows[0]["job_url"] == "https://egypt.tanqeeb.com/job/1" and rows[0]["payload"]["board"] == "Wuzzuf"


def test_naukrigulf_marks_remote_from_title_or_summary(monkeypatch):
    monkeypatch.setattr(gulf, "fetch", lambda method, url, **kw: Saved("naukrigulf.json"))
    rows = gulf.naukrigulf()
    assert titles(rows) == ["DATA ENGINEER – (DX)", "Data Engineer"]  # each once, though every keyword finds it
    assert rows[0]["payload"]["is_remote"] is False and "Dubai" in rows[0]["location"]


def test_gulftalent_keeps_only_your_roles(monkeypatch):
    monkeypatch.setattr(gulf, "get", lambda url, **params: Saved("gulftalent.json"))
    assert gulf.gulftalent() == []  # Database System Analyst, a technician, a recruiter: none yours


def test_dubizzle_keeps_your_roles_and_marks_them_remote(monkeypatch):
    monkeypatch.setattr(gulf, "fetch", lambda method, url, **kw: Saved("dubizzle.json"))
    rows = gulf.dubizzle()
    assert titles(rows) == ["Data Analyst"] and rows[0]["location"] == "Business Bay, Dubai, UAE (remote)"


def test_remoteco_reads_only_new_fully_remote_role_pages(monkeypatch):
    asked = []

    def get(url, **params):
        asked.append(url)
        if url.endswith("sitemap.xml"):
            return Saved("remoteco_sitemap.xml")
        job = json.loads((FIXTURES / "remoteco_job.json").read_text(encoding="utf-8"))
        job["remoteWorkLevel"] = "100REMWORK" if "71fc9d21" in url else "HYBREMWORK"
        return Saved(f'<script id="__NEXT_DATA__">{json.dumps({"props": {"pageProps": {"jobDetails": job}}})}</script>')
    monkeypatch.setattr(remote, "get", get)
    rows = remote.remoteco(seen=set())
    assert titles(rows) == ["Data Scientist"] and rows[0]["location"] == "United States (remote)"
    assert not any("staff-accountant" in url for url in asked)  # not your role: its page is never asked
    asked.clear()
    remote.remoteco(seen={r["job_url"] for r in rows})
    assert not any("71fc9d21" in url for url in asked)  # stored before: not asked again


def test_dailyremote_reads_cards_until_a_page_is_empty(monkeypatch):
    pages = iter([Saved("dailyremote.html"), Saved("<html></html>")])
    monkeypatch.setattr(remote, "get", lambda url, **params: next(pages))
    rows = remote.dailyremote()
    # the jobs open only to Latin America are out of scope; the one open worldwide is kept
    assert titles(rows) == ["Machine Learning Engineer"] and rows[0]["location"] == "Worldwide (remote)"
    assert all(r["company"] == "" and r["job_url"].startswith("https://dailyremote.com/remote-job/") for r in rows)


def test_an_empty_first_page_is_a_warning_not_a_quiet_day(monkeypatch, capsys):
    monkeypatch.setattr(remote, "get", lambda url, **params: Saved("<html></html>"))
    assert remote.dailyremote() == []
    assert "WARNING dailyremote: no job cards on its first page - has the site changed?" in capsys.readouterr().out


def test_wellfound_reads_next_data_with_company_size(monkeypatch):
    monkeypatch.setattr(startups, "WELLFOUND_ROLES", ["data-engineer"])
    monkeypatch.setattr(startups, "WELLFOUND_PAGES", 1)
    monkeypatch.setattr(startups, "get", lambda url, **params: Saved("wellfound.html"))
    rows = startups.wellfound()  # the Egypt page and page 1 give the same job: stored once
    assert titles(rows) == ["Data Engineer"] and rows[0]["company"] == "Acme AI"
    assert rows[0]["location"] == "Remote" and rows[0]["payload"]["company_size"] == "51-200"
    assert rows[0]["job_url"] == "https://wellfound.com/jobs/10-data-engineer"


def test_welcometothejungle_reads_algolia_with_headcount(monkeypatch):
    monkeypatch.setattr(startups, "KEYWORDS", ["data engineer"])
    monkeypatch.setattr(startups, "fetch", lambda method, url, **kw: Saved("welcometothejungle.json"))
    rows = startups.welcometothejungle()
    assert titles(rows) == ["Data Engineer"] and rows[0]["payload"]["company_size"] == "251"
    assert rows[0]["job_url"] == "https://www.welcometothejungle.com/en/companies/firstup/jobs/data-engineer_abc"
    assert rows[0]["location"] == "Remote: United States"


def test_startup_jobs_stops_at_the_window(monkeypatch):
    monkeypatch.setattr(startups, "STARTUP_JOBS_ROLES", ["data-engineer"])
    monkeypatch.setattr(startups, "since", lambda: "2026-01-01")  # the 2020 card is old
    monkeypatch.setattr(startups, "get", lambda url, **params: Saved("startup_jobs.html"))
    rows = startups.startup_jobs()
    assert [r["job_url"] for r in rows] == ["https://startup.jobs/data-engineer-acme-1"] and rows[0]["company"] == "Acme"


def test_builtin_and_ycombinator_read_ages_not_dates(monkeypatch):
    monkeypatch.setattr(startups, "since", lambda: str(date.today() - timedelta(days=1)))
    monkeypatch.setattr(startups, "get", lambda url, **params: Saved("builtin.html"))
    rows = startups.builtin()  # "5 Hours Ago" is in the window, "Reposted 9 Days Ago" is not
    assert [r["job_url"] for r in rows] == ["https://builtin.com/job/data-analyst/1"]
    assert rows[0]["location"] == "Remote: Austin, TX, USA"
    monkeypatch.setattr(startups, "YC_PAGES", ["data-science"])
    monkeypatch.setattr(startups, "get", lambda url, **params: Saved("ycombinator.html"))
    rows = startups.ycombinator()  # "about 3 hours" is in the window, "8 months" is not
    assert titles(rows) == ["Data Engineer"] and rows[0]["payload"]["is_remote"]


def test_companies_doc_matches_settings():
    script = Path(__file__).parent.parent / "scripts" / "companies_doc.py"
    spec = importlib.util.spec_from_file_location("companies_doc", script)
    doc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(doc)
    assert doc.DOC.read_text(encoding="utf-8") == doc.render(), "run: .venv\\Scripts\\python scripts\\companies_doc.py"
