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
