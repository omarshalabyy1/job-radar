r"""What the inbox reader takes as a job's employer. Run on the laptop:

    .venv\Scripts\python -m pytest tests/test_inbox.py
"""

import pytest

from job_radar.sources.inbox import company_of_subject, jobs_in_email


@pytest.mark.parametrize("subject, company", [
    ("New jobs posted from Initech Group", "Initech Group"),
    ("  new jobs posted from Acme  ", "Acme"),
    ("Your weekly digest", None),
    ("10 new jobs posted from your searches", None),
])
def test_the_employer_of_an_employer_alert_is_named_by_its_subject(subject, company):
    assert company_of_subject(subject) == company


def test_the_next_job_link_is_never_the_company():
    page = ('<a href="https://careers.example.com/job/Cairo-Data-Engineer/1/">Data Engineer - Cairo, EG</a>'
            '<a href="https://careers.example.com/job/Cairo-Service-Delivery-Manager/2/">Service Delivery Manager</a>')
    ((title, company, _, _),) = jobs_in_email(page)
    assert title == "Data Engineer - Cairo, EG" and company == ""


def test_a_company_link_after_the_job_is_still_the_company():
    page = ('<a href="https://x.example.com/viewjob?id=1">Data Engineer</a>'
            '<a href="https://x.example.com/company/acme">Acme</a>')
    ((_, company, _, _),) = jobs_in_email(page)
    assert company == "Acme"


def test_a_job_link_keeps_nothing_about_you():
    """2026-10-17: NaukriGulf alert links carried Omar's email, user id and an auto-login token. A stored link keeps
    only what names the job; a tracking redirect is unwrapped to the job page it carries."""
    from urllib.parse import quote
    from job_radar.sources.inbox import canonical_url
    job = "https://www.naukrigulf.com/data-engineer-jobs-in-cairo-egypt-in-acme-1-to-6-years-n-cd-1-jid-2"
    login = (f"https://www.naukrigulf.com/nglogin/user/mailerLogin?conmailer=SECRET&mailerId=me%40mail.com"
             f"&rUrl={quote(job + '?mailerId=ME&utm_source=alert', safe='')}")
    tracker = f"https://ccs-tracking.naukrigulf.com/?data=TOKEN&redirect={quote(login, safe='')}"
    assert canonical_url(tracker) == job
    assert canonical_url("https://eg.indeed.com/rc/clk/dl?jk=abc123&tk=T&alid=A&from=ja&rd=R&tmtk=X") == \
        "https://eg.indeed.com/viewjob?jk=abc123"
    assert canonical_url("https://www.bayt.com/en/egypt/jobs/data-engineer-1/?email_name=me&userToken=SECRET") == \
        "https://www.bayt.com/en/egypt/jobs/data-engineer-1/"
    assert canonical_url("https://himalayas.app/job/email?uid=1&alert_id=2&redirect=https%3A%2F%2Fhimalayas.app"
                         "%2Fcompanies%2Facme%2Fjobs%2Fdata-engineer") == "https://himalayas.app/companies/acme/jobs/data-engineer"
    assert canonical_url("https://wuzzuf.net/jobs/p/abc-Data-Engineer?o=1&l=sp&t=sj&a=TOKEN") == "https://wuzzuf.net/jobs/p/abc-Data-Engineer"
    assert canonical_url("https://boards.greenhouse.io/acme/jobs/1?gh_jid=1&autologin=x&token=y") == \
        "https://boards.greenhouse.io/acme/jobs/1?gh_jid=1"


def test_a_click_tracker_is_followed_and_never_stored(monkeypatch):
    from job_radar.sources import inbox
    answers = {"ok": "https://wellfound.com/jobs/1-data-engineer?utm_source=x", "login": "https://links.wellfound.com/s/c/X"}
    for case, final in answers.items():
        monkeypatch.setattr(inbox.requests, "head", lambda url, **kw: type("R", (), {"url": final})())
        assert inbox.followed("https://links.wellfound.com/s/c/TOKEN") == (
            "https://wellfound.com/jobs/1-data-engineer" if case == "ok" else None)

    def down(url, **kw):
        raise inbox.requests.ConnectionError("down")
    monkeypatch.setattr(inbox.requests, "head", down)
    assert inbox.followed("https://links.wellfound.com/s/c/TOKEN") is None  # skipped, with a warning


def test_a_line_of_body_text_is_never_a_location():
    """2026-10-17: 'To increase your chances of being accepted...' was stored as a location."""
    page = ('<p><a href="https://wuzzuf.net/jobs/p/1-Data-Analyst">Data Analyst</a></p><p>Acme</p>'
            '<p>To increase your chances of being accepted, add a short video.</p>')
    assert [location for _, _, location, _ in jobs_in_email(page)] == [""]
    page = page.replace("To increase your chances of being accepted, add a short video.", "Cairo, Egypt")
    assert [location for _, _, location, _ in jobs_in_email(page)] == ["Cairo, Egypt"]
