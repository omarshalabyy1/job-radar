"""extract_companies, 2026-11-14: the night 12 browser pages timed out together and four SuccessFactors sites said "0"
while their search had led to a login or maintenance page."""

import pytest

from job_radar import config, steps
from job_radar.sources import companies

from test_sources import Saved
from test_steps import RolledBack, conn  # noqa: F401  (the `conn` fixture)


def test_a_batch_never_mixes_api_sites_with_browser_pages(conn, monkeypatch):
    """extract_companies (rolled back): 49 API sites and 2 pages are not one batch of 50 run 12 at a time (the first site's
    kind set the batch's threads: 12 Chromiums started at once and all 12 pages ran out of time, 2026-11-13); the pages
    are a batch of their own, 4 at a time."""
    monkeypatch.setattr(steps, "online", lambda: True)
    monkeypatch.setitem(config.FEATURES, "eligibility", False)
    for i in range(49):
        conn.execute("INSERT INTO core.company (company, careers_url, platform, api) VALUES (%s, %s, 'greenhouse', 'x')",
                     (f"zz api {i:02}", f"https://test.example/api{i}"))
    for i in range(2):
        conn.execute("INSERT INTO core.company (company, careers_url, platform, api) VALUES (%s, %s, 'page', %s)",
                     (f"zz page {i}", f"https://test.example/page{i}", f"https://test.example/page{i}"))
    batches = []

    def company_sites(sites, threads=4):
        batches.append((threads, [s[2] for s in sites]))
        return [], [(s[2], s[3], None, s[0]) for s in sites]

    monkeypatch.setattr(steps.sources, "company_sites", company_sites)
    steps.extract_companies(RolledBack(conn))
    assert [(threads, sorted(set(kinds))) for threads, kinds in batches] == [(12, ["greenhouse"]), (4, ["page"])]


def test_a_successfactors_search_that_lands_elsewhere_fails_loudly(monkeypatch):
    """successfactors: jobs.ericsson.com/search/ answers 200 with a Microsoft sign-in page (and career5's with its maintenance
    page): no data rows, so the reader said "0" every night. A search that ends anywhere but the search page asked is an
    error, for the log and the site's note."""
    page = Saved("successfactors_login_redirect.html")
    page.url = "https://login.microsoftonline.com/92e84ceb-fbfd-47ab-be52-080c6b87953f/saml2?SAMLRequest=fZLLbtswEEX"
    monkeypatch.setattr(companies, "get", lambda url, **params: page)
    monkeypatch.setattr(companies.time, "sleep", lambda seconds: None)
    with pytest.raises(ValueError, match="login.microsoftonline.com"):
        companies.successfactors("Ericsson", "https://jobs.ericsson.com")
