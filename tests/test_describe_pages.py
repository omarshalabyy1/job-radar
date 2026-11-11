"""describe reads a SmartRecruiters job through its postings API: the job page builds its text in JavaScript and has no
schema.org JobPosting, so 554 of 554 pages gave no description on 2026-11-11."""
from pathlib import Path

from job_radar import steps

SAMPLE = (Path(__file__).parent / "fixtures" / "smartrecruiters_posting.json").read_bytes()


def test_a_smartrecruiters_job_page_is_read_through_its_postings_api():
    assert steps.page_url("https://jobs.smartrecruiters.com/SAPITBusinessSysteme/744000157855774") == \
        "https://api.smartrecruiters.com/v1/companies/SAPITBusinessSysteme/postings/744000157855774"
    assert steps.page_url("https://jobs.smartrecruiters.com/Coface/744000158-data-engineer") == \
        "https://api.smartrecruiters.com/v1/companies/Coface/postings/744000158"
    assert steps.page_url("https://boards.greenhouse.io/acme/jobs/1") == "https://boards.greenhouse.io/acme/jobs/1"


def test_the_postings_api_answer_gives_the_job_ad_text():
    text = steps.page_description(SAMPLE)
    assert text and len(text) > 300
    assert "<" not in text  # the sections' HTML is reduced to text


def test_a_page_without_a_job_posting_still_gives_none():
    assert steps.page_description(b"<html><body>no posting here</body></html>") is None
    assert steps.page_description(b'{"jobAd": {"sections": {}}}') is None
