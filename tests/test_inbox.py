r"""What the inbox reader takes as a job's employer. Run on the laptop:

    .venv\Scripts\python -m pytest tests/test_inbox.py
"""

import pytest

from job_radar.sources import inbox
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


def test_a_line_of_body_text_is_never_a_location():
    """2026-10-17: 'To increase your chances of being accepted...' was stored as a location."""
    page = ('<p><a href="https://wuzzuf.net/jobs/p/1-Data-Analyst">Data Analyst</a></p><p>Acme</p>'
            '<p>To increase your chances of being accepted, add a short video.</p>')
    assert [location for _, _, location, _ in jobs_in_email(page)] == [""]
    page = page.replace("To increase your chances of being accepted, add a short video.", "Cairo, Egypt")
    assert [location for _, _, location, _ in jobs_in_email(page)] == ["Cairo, Egypt"]


class FakeImap:
    """One inbox with one email, as imaplib answers."""
    mail = (b"From: Jobs <jobs@wuzzuf.net>\r\nSubject: New jobs\r\nMessage-ID: <m1@x>\r\nDate: Thu, 12 Nov 2026 10:00:00 +0000\r\n"
            b"Content-Type: text/html\r\n\r\n<a href=\"https://wuzzuf.net/jobs/p/1-Data-Analyst\">Data Analyst</a>")

    def __init__(self, host): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def login(self, *args): pass
    def list(self): return "OK", []
    def select(self, *args, **kwargs): pass

    def uid(self, command, *args):
        if command == "search":
            return "OK", [b"1"]
        head = self.mail.split(b"\r\n\r\n")[0]
        return "OK", [(b"1 (RFC822.SIZE 300 BODY[HEADER.FIELDS (FROM SUBJECT MESSAGE-ID DATE)] {1}", head if "HEADER" in args[1] else self.mail)]


def test_an_email_with_jobs_is_remembered_too(monkeypatch):
    """2026-11-12: an alert whose jobs were all stored from an earlier email left no posting of its own, so its
    Message-ID was in neither list and it was downloaded again on every run."""
    monkeypatch.setattr(inbox.imaplib, "IMAP4_SSL", FakeImap)
    remembered: set[str] = set()
    rows = inbox.read_mailbox("me@gmail.com", "x", "imap.gmail.com", set(), remembered)
    assert [r["title"] for r in rows] == ["Data Analyst"] and remembered == {"<m1@x>"}


def card(href: str, *lines: str) -> str:
    return f'<a href="{href}">' + "".join(f"<div>{line}</div>" for line in lines) + "</a>"


def test_a_glassdoor_card_names_the_company_first_then_the_title():
    """2026-11-12: a Glassdoor alert's 12 jobs gave none: its card is company, rating, title, place."""
    page = (card("https://www.glassdoor.com/partner/jobListing.htm?pos=101&jobListingId=1", "Acme", "3.8 ★",
                 "BI & Reporting Assoc Analyst", "New Cairo", "3d")
            + card("https://www.glassdoor.com/partner/jobListing.htm?pos=102&jobListingId=2", "Initech",
                   "Data Analyst", "Maadi Cornish", "Easy Apply", "11d"))
    assert [job[:3] for job in jobs_in_email(page)] == [
        ("BI & Reporting Assoc Analyst", "Acme", "New Cairo"), ("Data Analyst", "Initech", "Maadi Cornish")]
    post = card("https://www.glassdoor.com/Community/bowl-1/post-2", "Tech", "Data Analyst", "Anyone here worked at Acme?")
    assert jobs_in_email(post) == []  # a community post is no job: only a job link's card is read this way


def test_a_workable_card_says_at_before_the_company():
    """2026-11-12: Workable's "Your Jobs Update" gave the company "at"."""
    page = card("https://email-t.workablemail.com/c/abc", "Power BI Developer", "at", "Acme", "·", "Hybrid", "·",
                "Nasr City, Al Manteqah Al Oula, Egypt")
    assert [job[:3] for job in jobs_in_email(page)] == [("Power BI Developer", "Acme", "Nasr City, Al Manteqah Al Oula, Egypt")]


def test_a_linkedin_hiring_message_is_a_job():
    page = ('<a href="https://www.linkedin.com/comm/jobs/view/4487641128/?x=1">Sam is hiring for the Data Analyst'
            ' job at Acme.</a><a href="https://www.linkedin.com/comm/jobs/view/4487641128/?x=2">Message Sam</a>')
    assert jobs_in_email(page) == [("Data Analyst", "Acme", "", "https://www.linkedin.com/jobs/view/4487641128")]
