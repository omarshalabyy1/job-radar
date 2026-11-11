"""Career sites the daily run read as no job at all although they have one (2026-11-10 audit of the 'page' companies): a
site already stored as 'page' whose address names its platform, a new page rendered twice inside one site budget, and the
readers for Pinpoint, Oracle Recruiting (Raya, Mashreq, Orascom), Eightfold (Vodafone), Zoho Recruit (Aqarmap, Valify),
Taleo (CIB) and Workday on myworkdaysite (Mondelez). Every reader runs on a saved sample (tests/fixtures/), without the
network."""

import json
from datetime import date
from types import SimpleNamespace

import pytest

from job_radar import config
from job_radar.sources import base, companies

from test_sources import Saved
from test_limits import web  # noqa: F401  (the `web` fixture)


@pytest.fixture(autouse=True)
def every_title_a_role(monkeypatch):
    monkeypatch.setattr(companies, "role_of", lambda title: 1)


def asked_for(monkeypatch, name: str, answers: dict | None = None):
    """companies.fetch answers every call with the saved sample (or answers[url part] when it names one); the calls are returned."""
    asked = []

    def fetch(method, url, **kwargs):
        asked.append((method, url, kwargs))
        return Saved(next((v for k, v in (answers or {}).items() if k in url or k in json.dumps(kwargs.get("json", ""))), name))

    monkeypatch.setattr(companies, "fetch", fetch)
    monkeypatch.setattr(companies, "get", lambda url, **params: fetch("GET", url, params=params))
    return asked


# -- a stored 'page' row and a new page ------------------------------------------------------------------------------

def test_a_page_row_whose_address_names_a_platform_is_read_by_that_platform(monkeypatch, web):
    """Elmenus was stored as 'page' before Recruitee had a reader; detect() is never run again for a 'page', so the row
    stayed 'page' for good (Brimore, Valu and Synapse the same)."""
    read = []
    monkeypatch.setitem(companies.READERS, "recruitee", lambda company, slug: read.append(slug) or [])
    rows, [found] = companies.company_sites([("Elmenus", "https://elmenus.recruitee.com/", "page", "https://elmenus.recruitee.com/")])
    assert read == ["elmenus"]
    assert found[:2] == ("recruitee", "elmenus")


def test_a_page_row_on_no_known_platform_stays_a_page(monkeypatch, web):
    monkeypatch.setattr(companies, "rendered", lambda url, click=None: "<html></html>")
    _, [found] = companies.company_sites([("Acme", "https://acme.example/careers", "page", "https://acme.example/careers")])
    assert found[:2] == ("page", "https://acme.example/careers")


def test_a_new_career_page_is_rendered_once_for_its_detection_and_its_read(monkeypatch, web):
    """The 14 'read failed: the site's time is spent' sites (ArabyAds, Kashier ...): detect() rendered the page and
    career_page rendered it again inside the same 60 seconds."""
    rendered = []
    monkeypatch.setattr(companies, "rendered", lambda url, click=None: rendered.append(url) or "<html><body>Careers</body></html>")
    _, [found] = companies.company_sites([("Acme", "https://acme.example/careers", None, None)])
    assert found[0] == "page"
    assert rendered == ["https://acme.example/careers"]


# -- a job that names no place ---------------------------------------------------------------------------------------

def test_a_job_on_a_page_that_names_no_place_anywhere_is_the_companys_own_in_egypt(monkeypatch):
    """Halan, 34ML, Intcore: the page names no place at all, so its jobs were dropped for naming none."""
    page = '<h1>Join us</h1><ul><li><a href="/position/data-engineer/">Senior Data Engineer</a></li></ul>'
    monkeypatch.setattr(companies, "rendered", lambda url, click=None: page)
    rows = companies.career_page("Halan", "https://jobs.halan.com/")
    assert [(r["title"], r["location"]) for r in rows] == [("Senior Data Engineer", config.HOME)]


def test_a_job_naming_no_place_on_a_page_that_names_other_places_is_still_left_out(monkeypatch):
    """Many of your companies hire abroad too: with a place named anywhere on the page, a bare job is not assumed Egyptian."""
    page = ('<ul><li><a href="/p/1">Senior Data Engineer</a></li></ul><footer>Offices: Dubai, London</footer>')
    monkeypatch.setattr(companies, "rendered", lambda url, click=None: page)
    assert companies.career_page("Acme", "https://acme.example/careers") == []


def test_a_job_takes_the_place_its_card_names_when_the_link_sits_beside_it(monkeypatch):
    """Halan and ITWorx: the title link is in a heading and the city in the next element of the same card, never in the
    link's own parent; the neighbouring card's city is not its."""
    page = ('<div class="list"><div class="card"><h3><a href="/p/1">Senior Data Engineer</a></h3><p>Sheikh Zayed, Full Time</p></div>'
            '<div class="card"><h3><a href="/p/2">Senior Data Analyst</a></h3><p>Dubai, Full Time</p></div></div>')
    monkeypatch.setattr(companies, "rendered", lambda url, click=None: page)
    rows = companies.career_page("Halan", "https://jobs.halan.com/")
    assert [(r["title"], r["location"]) for r in rows] == [("Senior Data Engineer", config.HOME), ("Senior Data Analyst", "UAE")]


def test_a_job_in_a_long_list_does_not_borrow_the_place_of_the_list(monkeypatch):
    jobs = "".join(f'<li><a href="/p/{i}">Senior Sales Manager {i}</a> {"x" * 60}</li>' for i in range(12))
    page = f'<ul><li><h3><a href="/p/data">Senior Data Engineer</a></h3></li>{jobs}<li>Dubai</li></ul>'
    monkeypatch.setattr(companies, "rendered", lambda url, click=None: page)
    monkeypatch.setattr(companies, "role_of", lambda title: "Data" in title or None)
    assert companies.career_page("Acme", "https://acme.example/careers") == []


def test_a_category_page_of_a_careers_site_is_not_a_job(monkeypatch):
    """Sonic's 'Data Analytics & Insights (2)' links to a category of its blog, not to a job."""
    page = '<h1>Careers</h1><a href="/category/data-analytics-insights/">Data Analytics &amp; Insights (2)</a>'
    monkeypatch.setattr(companies, "rendered", lambda url, click=None: page)
    assert companies.career_page("Sonic", "https://sonic-tec.com/career/") == []


def test_a_teamtailor_site_on_its_own_domain_is_detected_from_its_feed_and_read_there(monkeypatch, web):
    """ArabyAds and Convertedin keep their jobs on careers.<own domain>, not <slug>.teamtailor.com."""
    feed = Saved("teamtailor_propertyfinder.xml").text.replace("propertyfinder.teamtailor.com", "careers.example.com")
    monkeypatch.setattr(companies, "request", lambda method, url, **kw: SimpleNamespace(status_code=200, text=feed, headers={}))
    assert companies.detect("https://careers.example.com/jobs.rss")[:2] == ("teamtailor", "careers.example.com")
    asked = asked_for(monkeypatch, "teamtailor_propertyfinder.xml")
    rows = companies.READERS["teamtailor"]("Example", "careers.example.com")
    assert asked[0][1] == "https://careers.example.com/jobs.rss" and len(rows) == 5


# -- the platforms behind the pages ----------------------------------------------------------------------------------

@pytest.mark.parametrize("url, expected", [
    ("https://tabby.pinpointhq.com/", ("pinpoint", "tabby")),
    ("https://moneyfellows.pinpointhq.com/en/jobs/475910", ("pinpoint", "moneyfellows")),
    ("https://aqarmap.zohorecruit.com/jobs/Careers", ("zoho", "https://aqarmap.zohorecruit.com/jobs/Careers")),
    ("https://cibeg.taleo.net/careersection/cib_ex/jobsearch.ftl?lang=en", ("taleo", "https://cibeg.taleo.net/careersection/cib_ex")),
    ("https://wd3.myworkdaysite.com/recruiting/mdlz/External?locationCountry=x", ("workday", "https://wd3.myworkdaysite.com/recruiting/mdlz/External")),
    ("https://www.ibm.com/careers/search?field_keyword_05[0]=Egypt", ("ibm", "https://www.ibm.com/careers/search?field_keyword_05[0]=Egypt")),
    ("https://jobs.vodafone.com/careers?location=Egypt&domain=vodafone.com",
     ("eightfold", "https://jobs.vodafone.com/careers?location=Egypt&domain=vodafone.com")),
    ("https://enterpriseplatform.dell.com/hcmUI/CandidateExperience/en/sites/careers/jobs?location=Egypt&locationId=300000000464933",
     ("oracle_hcm", "https://enterpriseplatform.dell.com/hcmUI/CandidateExperience/en/sites/careers/jobs?location=Egypt&locationId=300000000464933")),
    ("https://hcld.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs?location=Egypt&locationId=300000000281972",
     ("oracle_hcm", "https://hcld.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs?location=Egypt&locationId=300000000281972")),
])
def test_a_careers_address_names_its_platform(url, expected):
    assert companies.detect(url)[:2] == expected


def test_a_pinpoint_board_is_read_from_its_postings_json(monkeypatch):
    asked = asked_for(monkeypatch, "pinpoint_tabby.json")
    rows = companies.READERS["pinpoint"]("Tabby", "tabby")
    assert asked[0][1] == "https://tabby.pinpointhq.com/postings.json"
    assert [(r["title"], config.place_of(r["location"])) for r in rows] == [
        ("Customer Service Ambassador", config.HOME), ("Instructional Designer", config.HOME),
        ("Senior Analyst, Vendor Management", "Saudi Arabia")]  # its 'Remote' job names no region: out of scope, as on every board
    assert rows[0]["job_url"].startswith("https://tabby.pinpointhq.com/en/postings/")


def test_oracle_recruiting_is_read_through_its_requisitions_api_with_the_location_the_address_names(monkeypatch):
    asked = asked_for(monkeypatch, "oracle_hcm_raya.json")
    url = ("https://fa-ewrh-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/RayaCareers/jobs"
           "?location=Egypt&locationId=300000000462184&locationLevel=country&mode=location")
    rows = companies.READERS["oracle_hcm"]("Raya", url)
    api = asked[0][1]
    assert api.startswith("https://fa-ewrh-saasfaprod1.fa.ocs.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions?")
    assert "siteNumber=RayaCareers" in api and "locationId=300000000462184" in api and "sortBy=POSTING_DATES_DESC" in api
    assert [r["title"] for r in rows][:2] == ["Raya Returnship Program 2027", "Head (Investments & Investor Relations)"]
    assert (rows[0]["location"], rows[0]["date_posted"]) == ("Egypt", "2026-11-08")
    assert rows[0]["job_url"] == "https://fa-ewrh-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/RayaCareers/job/" + str(rows[0]["payload"]["Id"])


def test_oracle_recruiting_without_a_location_in_the_address_asks_for_none(monkeypatch):
    asked = asked_for(monkeypatch, "oracle_hcm_orascom.json")
    rows = companies.READERS["oracle_hcm"]("Orascom Construction", "https://fa-esco-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs")
    assert "locationId" not in asked[0][1] and len(rows) == 5


def test_an_eightfold_site_is_read_page_by_page_from_its_search_api(monkeypatch):
    sample = Saved("eightfold_vodafone.json").text
    empty = json.dumps({"status": 200, "data": {"count": 37, "positions": []}})
    asked = []

    def fetch(method, url, **kwargs):
        asked.append(url)
        return Saved(sample if "start=0" in url else empty)

    monkeypatch.setattr(companies, "fetch", fetch)
    rows = companies.READERS["eightfold"]("Vodafone", "https://jobs.vodafone.com/careers?location=Egypt&domain=vodafone.com")
    assert asked[0].startswith("https://jobs.vodafone.com/api/pcsx/search?") and "domain=vodafone.com" in asked[0]
    assert "location=Egypt" in asked[0] and "sort_by=timestamp" in asked[0]
    assert len(asked) == 4  # 37 jobs, 10 a page: starts 0, 10, 20, 30
    assert (rows[0]["title"], rows[0]["location"], rows[0]["date_posted"]) == ("AI & Automation Engineer - VOIS", "Giza, Egypt", "2026-11-09")
    assert rows[0]["job_url"] == "https://jobs.vodafone.com/careers/job/" + str(rows[0]["payload"]["id"])


def test_a_zoho_recruit_page_carries_its_jobs_as_json_in_one_input(monkeypatch):
    asked_for(monkeypatch, "zoho_aqarmap.html")
    [job] = companies.READERS["zoho"]("Aqarmap", "https://aqarmap.zohorecruit.com/jobs/Careers")
    assert (job["title"], job["location"], job["date_posted"]) == ("Property Consultant", "Cairo, Egypt", "2022-11-17")
    assert job["job_url"].startswith("https://aqarmap.zohorecruit.com/jobs/Careers/")


def test_taleo_is_read_through_its_rest_search_with_the_portal_its_page_names(monkeypatch):
    asked = asked_for(monkeypatch, "taleo_cib.json", {"jobsearch.ftl": "taleo_cib_page.html"})
    rows = companies.READERS["taleo"]("CIB", "https://cibeg.taleo.net/careersection/cib_ex")
    assert asked[0][1] == "https://cibeg.taleo.net/careersection/cib_ex/jobsearch.ftl"
    method, url, kwargs = asked[1]
    assert (method, url) == ("POST", "https://cibeg.taleo.net/careersection/rest/jobboard/searchjobs?lang=en&portal=8105020225")
    assert kwargs["json"]["pageNo"] == 1
    assert [kw["json"]["pageNo"] for _, _, kw in asked[1:]] == [1, 2, 3, 4]  # 82 jobs, 25 a page
    ai = next(r for r in rows if r["title"].startswith("AI Solution Architect"))
    assert (ai["location"], ai["job_url"]) == ("Egypt-Giza-GIZA HEAD OFFICE", "https://cibeg.taleo.net/careersection/cib_ex/jobdetail.ftl?job=260002IL")


def test_a_classic_taleo_page_with_no_portal_is_read_from_the_list_in_the_page(monkeypatch):
    """e& Egypt's Taleo has no REST search (careerSectionUnAvailable): its newest 100 jobs are in the page itself."""
    asked = asked_for(monkeypatch, "taleo_etisalat_page.html")
    rows = companies.READERS["taleo"]("e& Egypt", "https://etisalatmisr.taleo.net/careersection/ex")
    assert [(m, u, kw.get("params")) for m, u, kw in asked] == [
        ("GET", "https://etisalatmisr.taleo.net/careersection/ex/jobsearch.ftl", {"lang": "en", "dropListSize": 100})]
    bi = next(r for r in rows if r["title"].startswith("BI System"))
    assert (bi["title"], bi["location"], bi["date_posted"], bi["job_url"]) == (
        "BI System Senior Analyst.Enterprise Information Management", "Egypt", "2026-08-24",
        "https://etisalatmisr.taleo.net/careersection/ex/jobdetail.ftl?job=254917")
    assert any(r["title"] == "Segmentation & Data Mining Senior Specialist.Decision Support" for r in rows)  # %26 is &


def test_a_taleo_page_that_names_no_portal_fails_loudly_instead_of_reading_no_job(monkeypatch):
    asked_for(monkeypatch, "<html>no portal here</html>")
    with pytest.raises(ValueError, match="portal"):
        companies.READERS["taleo"]("ADIB", "https://adibegypt.taleo.net/careersection/ex")


def test_a_workday_site_on_myworkdaysite_is_read_through_its_cxs_search(monkeypatch):
    asked = asked_for(monkeypatch, "workday_mondelez.json")
    rows = companies.READERS["workday"]("Mondelez", "https://wd3.myworkdaysite.com/recruiting/mdlz/External")
    method, url, kwargs = asked[0]
    assert (method, url) == ("POST", "https://wd3.myworkdaysite.com/wday/cxs/mdlz/External/jobs")
    assert kwargs["json"]["searchText"] == "Egypt"
    assert [(r["title"], r["location"]) for r in rows][:2] == [("Head of Sales and RTM - Egypt", "Cairo, Egypt"),
                                                                ("Sr Analyst, SC Finance & External Manufacturing, MENAP", "Cairo, Egypt")]
    assert rows[2]["date_posted"] == str(date.today())  # "Posted Today"
    assert rows[0]["job_url"].startswith("https://wd3.myworkdaysite.com/recruiting/mdlz/External/job/Cairo-Egypt/")


def test_ibm_is_read_through_its_careers_search_api_for_the_country_its_address_names(monkeypatch):
    """IBM's careers page builds its list in the browser: the rendered page showed no job, its search API has them."""
    url = "https://www.ibm.com/careers/search?field_keyword_05[0]=Egypt"
    assert companies.detect(url)[:2] == ("ibm", url)
    asked = asked_for(monkeypatch, "ibm_egypt.json")
    rows = companies.READERS["ibm"]("IBM", url)
    method, api, kwargs = asked[0]
    assert (method, api) == ("POST", "https://www-api.ibm.com/search/api/v2")
    assert {"term": {"field_keyword_05": "Egypt"}} in kwargs["json"]["query"]["bool"]["must"]
    assert [(r["title"], r["location"], r["date_posted"]) for r in rows][0] == ("Data Engineer-Data Integration", "Egypt (hybrid)", "2026-11-08")
    assert rows[0]["job_url"] == "https://careers.ibm.com/careers/JobDetail?jobId=140863"
