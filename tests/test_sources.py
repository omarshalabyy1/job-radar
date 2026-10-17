"""Each reader against a saved sample of its site (tests/fixtures/), without the network: a site that
changes its layout or API fails here first. Run on the laptop: .venv\\Scripts\\python -m pytest tests
"""

import importlib.util
import json
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

from job_radar.sources import companies, egypt, gulf, remote, startups

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
    from job_radar import config
    monkeypatch.setattr(gulf, "get", lambda url, **params: Saved("gulftalent.json"))
    monkeypatch.setitem(config.FEATURES, "market", False)
    assert gulf.gulftalent() == []  # Database System Analyst, a technician, a recruiter: none yours
    monkeypatch.setitem(config.FEATURES, "market", True)  # the website's collection keeps the other tech job too
    assert [r["title"] for r in gulf.gulftalent()] == ["Database System Analyst"]


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


def test_a_classic_successfactors_portal_is_read_after_its_search(monkeypatch):
    url = "https://career2.successfactors.eu/career?company=egyptiangu"
    assert companies.detect(url) == ("successfactors_classic", url, None)  # from its address alone
    pressed = []
    monkeypatch.setattr(companies, "rendered", lambda u, click=None: pressed.append(click) or Saved("successfactors_classic.html").text)
    monkeypatch.setattr(companies, "since", lambda: "2026-10-01")  # the two September jobs are old
    monkeypatch.setattr(companies, "role_of", lambda title: 1 if "Analyst" in title else None)
    rows = companies.successfactors_classic("EG Bank", url)
    assert pressed and "Search" in pressed[0]
    assert titles(rows) == ["Assistant Analyst - Credit School Program"]
    assert rows[0]["date_posted"] == "2026-10-13" and rows[0]["location"] == "Egypt"
    assert rows[0]["job_url"] == ("https://career2.successfactors.eu/career?career_ns=job_listing&company=egyptiangu"
                                  "&career_job_req_id=4250")


def test_smartrecruiters_recruitee_and_personio_read_their_public_apis(monkeypatch):
    """The three boards Radarak reads that the career sites did not: detected from their address, then read."""
    assert companies.detect("https://jobs.smartrecruiters.com/BoschGroup") == ("smartrecruiters", "BoschGroup", None)
    assert companies.detect("https://acme.recruitee.com/") == ("recruitee", "acme", None)
    assert companies.detect("https://acme.jobs.personio.de/") == ("personio", "acme", None)
    pages = {
        "https://api.smartrecruiters.com/v1/companies/BoschGroup/postings": json.dumps({"totalFound": 1, "content": [
            {"id": "7", "name": "Data Engineer", "releasedDate": "2026-10-15T11:11:44Z",
             "location": {"fullLocation": "Cairo, Egypt", "remote": False, "hybrid": True}}]}),
        "https://acme.recruitee.com/api/offers/": json.dumps({"offers": [
            {"title": "Data Analyst", "location": "Cairo, Egypt", "remote": True, "careers_url": "https://acme.com/o/1",
             "published_at": "2026-10-16 11:35:00 UTC", "description": "<p>SQL and Python</p>"},
            {"title": "Data Analyst", "location": "Cairo, Egypt", "careers_url": None}]}),  # no link: left out
        "https://acme.jobs.personio.de/xml": "<workzag-jobs><position><id>9</id><office>Cairo</office><additionalOffices>"
            "<office>Giza</office></additionalOffices><name>BI Developer</name><jobDescriptions><jobDescription><name>You</name>"
            "<value>&lt;p&gt;Power BI&lt;/p&gt;</value></jobDescription></jobDescriptions><createdAt>2026-10-14T10:00:00+00:00"
            "</createdAt></position></workzag-jobs>"}
    monkeypatch.setattr(companies, "get", lambda url, **params: Saved(pages[url]))
    monkeypatch.setattr(companies, "role_of", lambda title: 1)
    monkeypatch.setattr(companies, "place_of", lambda location: "Egypt")
    [bosch] = companies.smartrecruiters("Bosch", "BoschGroup")
    assert (bosch["location"], bosch["job_url"], bosch["date_posted"]) == (
        "Cairo, Egypt (hybrid)", "https://jobs.smartrecruiters.com/BoschGroup/7", "2026-10-15")
    [acme] = companies.recruitee("Acme", "acme")
    assert (acme["location"], acme["description"], acme["date_posted"]) == ("Cairo, Egypt (remote)", "SQL and Python", "2026-10-16")
    [bi] = companies.personio("Acme", "acme")
    assert (bi["location"], bi["job_url"], bi["description"]) == ("Cairo, Giza", "https://acme.jobs.personio.de/job/9", "Power BI")


def test_board_tokens_are_read_from_links_and_pages_once_each():
    from job_radar.sources import market
    text = ("https://boards.greenhouse.io/Stripe/jobs/1 https://job-boards.eu.greenhouse.io/embed/job_board?for=acme "
            "https://jobs.lever.co/spotify/a https://apply.workable.com/j/ABC https://apply.workable.com/acme-co/ "
            "https://www.recruitee.com https://channable.recruitee.com/o/x https://jobs.smartrecruiters.com/BoschGroup/1 "
            "https://boards.greenhouse.io/stripe/jobs/2")
    assert market.boards_in(text) == [("greenhouse", "stripe"), ("greenhouse", "acme"), ("lever", "spotify"),
                                      ("smartrecruiters", "BoschGroup"), ("workable", "acme-co"), ("recruitee", "channable")]


def test_a_wide_board_read_keeps_tech_jobs_names_its_board_and_leaves_the_text_out_of_the_payload(monkeypatch):
    from job_radar.sources import market
    jobs = {"jobs": [{"title": "Backend Engineer", "location": "Berlin", "isRemote": False, "workplaceType": "Hybrid",
                      "jobUrl": "https://jobs.ashbyhq.com/acme/1", "publishedAt": "2026-10-15T10:00:00Z",
                      "descriptionPlain": "Go and Postgres. " * 100, "isListed": True},
                     {"title": "Account Executive", "location": "Berlin", "jobUrl": "https://jobs.ashbyhq.com/acme/2"}]}
    monkeypatch.setattr(market, "get", lambda url, **params: Saved(json.dumps(jobs)))
    [job] = market.ashby("acme", "Acme")
    assert (job["source"], job["location"], job["date_posted"]) == ("ashby-board", "Berlin (hybrid)", "2026-10-15")
    assert job["description"].startswith("Go and Postgres.") and "descriptionPlain" not in job["payload"]
    assert job["payload"]["board"] == "ashby:acme"


def test_apify_items_become_wide_postings_keyed_by_their_link_and_the_budget_stops_a_run(monkeypatch):
    from job_radar.sources import apify
    items = [{"title": "Data Engineer", "companyName": "Acme", "location": "Berlin, Germany", "postedAt": "2026-10-16",
              "link": "https://www.linkedin.com/jobs/view/1?trk=x", "descriptionText": "Spark and SQL."},
             {"positionName": "QA Engineer", "company": "Beta", "jobLocation": "Remote", "url": "https://de.indeed.com/viewjob?jk=2",
              "description": "<p>Cypress</p>"},
             {"title": "No link"}]
    rows = apify.normalize("linkedin", items)
    assert [(r["source"], r["title"], r["job_url"], r["description"]) for r in rows] == [
        ("apify:linkedin", "Data Engineer", "https://www.linkedin.com/jobs/view/1", "Spark and SQL."),
        ("apify:linkedin", "QA Engineer", "https://de.indeed.com/viewjob?jk=2", "Cypress")]
    monkeypatch.setitem(apify.SETTINGS, "apify", {"monthly_budget_usd": 5, "boards": {
        "linkedin": {"enabled": True, "actor": "a/b", "input": {}, "max_items": 10, "max_charge_usd": 0.5}}})
    monkeypatch.setenv("APIFY_TOKEN", "test")
    monkeypatch.setattr(apify, "spent", lambda token: 4.8)  # $4.80 + $0.50 would pass $5: no run
    monkeypatch.setattr(apify, "run", lambda *a: pytest.fail("a run started over the budget"))
    assert apify.apify_jobs() == []


def test_a_workday_site_is_read_through_its_search_and_named_by_tenant_and_site(monkeypatch):
    from job_radar.sources import market
    assert market.boards_in("https://genpact.wd108.myworkdayjobs.com/en-US/External_Careers/job/x") == [
        ("workday", "genpact.wd108/External_Careers")]
    asked = []

    def fetch(method, url, **kw):
        asked.append((url, kw["json"]["searchText"], kw["json"]["offset"]))
        jobs = [{"title": "Data Engineer", "externalPath": "/job/Cairo-Egypt/Data-Engineer_JR1", "locationsText": "Cairo, Egypt",
                 "postedOn": "Posted Today"},
                {"title": "Accountant", "externalPath": "/job/Cairo-Egypt/Accountant_JR2", "locationsText": "2 Locations",
                 "postedOn": "Posted 3 Days Ago"}]
        return Saved(json.dumps({"jobPostings": jobs if kw["json"]["searchText"] == "data" else []}))
    monkeypatch.setattr(market, "fetch", fetch)
    [job] = market.workday("genpact.wd108/External_Careers", "Genpact")
    assert asked[0][0] == "https://genpact.wd108.myworkdayjobs.com/wday/cxs/genpact/External_Careers/jobs"
    assert (job["source"], job["location"], job["job_url"]) == (
        "workday", "Cairo, Egypt", "https://genpact.wd108.myworkdayjobs.com/External_Careers/job/Cairo-Egypt/Data-Engineer_JR1")
    assert len(asked) == len(market.WORKDAY_SEARCHES)  # a search with fewer than 20 hits is one call
