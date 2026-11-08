"""The Ashby form adapter against two saved real forms (tests/fixtures/apply/ashby_thndr.html, ashby_qlub.html: jobs.ashbyhq.com,
saved 2026-11-08) and one with hostile text added. Quirks of the board these pin: the page carries Google reCAPTCHA Enterprise (an
invisible badge, no checkbox), so the real forms are `needs_human`; the "Autofill from resume" upload above the form is never used;
the form lives at <posting>/application."""

from dataclasses import replace

import pytest
from apply_page import (browser, clicks, count_clicks_on, fast_waits, fixture_html, job, labels, page, resolver,  # noqa: F401
                        uploaded,
                        value_of)

from job_radar.apply import queue
from job_radar.apply.adapters import ashby
from job_radar.apply.adapters.base import Field, Receipt

SUBMIT = "button.ashby-application-form-submit-button"
THNDR = "https://jobs.ashbyhq.com/thndr/706a3396-c21e-4803-8648-f6e590e41b08/application"
APPROVED = {"name": "Omar Shalaby", "email": "omar@example.com", "linkedin": "https://linkedin.com/in/omar", "notice": "1 month"}
NAME, EMAIL, LINKEDIN, WHY = "#_systemfield_name", "#_systemfield_email", '[id="d9d1ed49-b035-4c32-99bb-372675750d72"]', \
    '[id="d85b2a15-ba13-4923-b3f1-b8a57b9c7044"]'
NOTICE, CURRENT, EXPECTED = ('[id="20db8075-d7af-43a7-8bb0-56a19d7db12b"]', '[id="36ab49ee-79e8-45e5-91c9-49997b7c38d3"]',
                             '[id="20e7e231-c51d-48ac-b1d1-272094637780"]')
RESUME = "#_systemfield_resume"
REMOVE_CAPTCHA = """() => document.querySelectorAll('iframe, .grecaptcha-badge, textarea[name="g-recaptcha-response"]').forEach(e => e.remove())"""
# what the board's own script does after a good submit (not in the saved DOM): the form is replaced by a message
SUCCESS_ON_CLICK = """(sel) => document.querySelector(sel).addEventListener('click', e => { e.preventDefault();
    document.querySelector('#form').innerHTML = '<div class="ashby-application-form-success-container">Your application was ' +
    'successfully submitted. We\\'ll contact you if there\\'s a match.</div>'; })"""


@pytest.fixture
def adapter():
    return ashby.Adapter()


def test_name_is_what_the_queue_calls_the_board(adapter):
    assert adapter.name == queue.board_of(THNDR) == "ashby"


def test_can_handle_ashby_posting_urls_only(adapter):
    assert adapter.can_handle(THNDR)
    assert adapter.can_handle("https://jobs.ashbyhq.com/qlub/cb15ed05-573c-4f78-99e4-49e1cff92d6f")
    assert not adapter.can_handle("https://boards.greenhouse.io/acme/jobs/1")
    assert not adapter.can_handle("https://ashbyhq.com.evil.example/jobs")
    assert not adapter.can_handle("https://evil.example/?next=jobs.ashbyhq.com")


@pytest.mark.parametrize("name", ["ashby_thndr.html", "ashby_qlub.html"])
def test_the_real_forms_carry_recaptcha_so_they_need_a_human(adapter, page, name):
    page.set_content(fixture_html(name))
    assert adapter.needs_human(page) is True


@pytest.mark.parametrize("name", ["ashby_thndr.html", "ashby_qlub.html"])
def test_without_the_captcha_widget_the_form_does_not_need_a_human(adapter, page, name):
    page.set_content(fixture_html(name))
    page.evaluate(REMOVE_CAPTCHA)
    assert adapter.needs_human(page) is False


def test_a_login_wall_or_a_verify_human_page_needs_a_human(adapter, page):
    page.set_content("<h1>Sign in to apply</h1><form><input name=email><input type=password name=password></form>")
    assert adapter.needs_human(page)
    page.set_content("<title>Just a moment...</title><h1>Verify you are human</h1><p>Checking your browser before accessing the site.</p>")
    assert adapter.needs_human(page)
    page.set_content("<title>Error 429</title><h1>Too many requests</h1>")
    assert adapter.needs_human(page)


def test_a_page_with_no_form_is_not_ready_to_submit(adapter, page, job):
    page.set_content("<h1>Sorry, this job is no longer accepting applications</h1>")
    missing = adapter.fill(page, job, resolver(APPROVED))
    assert missing and missing[0].required  # [] would let the runner click a button that is not there


def test_fill_answers_come_only_from_resolve_and_the_rest_is_returned(adapter, page, job):
    page.set_content(fixture_html("ashby_thndr.html"))
    page.evaluate(REMOVE_CAPTCHA)
    resolve = resolver(APPROVED)
    missing = adapter.fill(page, job, resolve)
    assert value_of(page, NAME) == "Omar Shalaby"
    assert value_of(page, EMAIL) == "omar@example.com"
    assert value_of(page, LINKEDIN) == "https://linkedin.com/in/omar"
    assert value_of(page, NOTICE) == "1 month"
    assert [value_of(page, s) for s in (WHY, CURRENT, EXPECTED)] == ["", "", ""]  # no approved answer: left empty
    assert labels(missing) == ["Why Thndr?", "Current Salary", "Expected Salary"]
    assert all(isinstance(f, Field) and f.required for f in missing)
    assert {q for q, _, _ in resolve.calls} >= {"Name", "Email", "LinkedIn profile", "Why Thndr?", "Notice Period"}


def test_fill_with_no_approved_answers_types_nothing_and_returns_every_required_field(adapter, page, job):
    page.set_content(fixture_html("ashby_qlub.html"))
    page.evaluate(REMOVE_CAPTCHA)
    missing = adapter.fill(page, job, resolver({}))
    assert labels(missing) == ["Name", "Email", "Share Your LinkedIn URL", "What are your salary expectations for this role in USD?"]
    assert page.locator("input[type=text], input[type=email]").evaluate_all("els => els.map(e => e.value)") == [""] * 4


def test_fill_uploads_the_tailored_cv_and_never_the_autofill_box(adapter, page, job):
    page.set_content(fixture_html("ashby_thndr.html"))
    page.evaluate(REMOVE_CAPTCHA)
    adapter.fill(page, job, resolver(APPROVED))
    assert uploaded(page, RESUME) == ["Omar Shalaby Acme CV.pdf"]
    assert page.locator(".ashby-application-form-autofill-pane input[type=file]").evaluate("e => e.files.length") == 0


def test_a_missing_cv_file_is_returned_not_skipped(adapter, page, job, tmp_path):
    page.set_content(fixture_html("ashby_thndr.html"))
    page.evaluate(REMOVE_CAPTCHA)
    gone = replace(job, cv_pdf=tmp_path / "gone.pdf")
    missing = adapter.fill(page, gone, resolver(APPROVED))
    assert ("Resume", "file") in [(f.label, f.kind) for f in missing]
    assert uploaded(page, RESUME) == []


def test_an_optional_question_with_no_answer_is_not_returned(adapter, page, job):
    page.set_content(fixture_html("ashby_thndr.html"))
    page.evaluate(REMOVE_CAPTCHA)
    page.evaluate("""() => document.querySelector('[id="36ab49ee-79e8-45e5-91c9-49997b7c38d3"]').removeAttribute('required')""")
    assert "Current Salary" not in labels(adapter.fill(page, job, resolver(APPROVED)))


def test_fill_never_clicks_submit(adapter, page, job):
    page.set_content(fixture_html("ashby_thndr.html"))
    page.evaluate(REMOVE_CAPTCHA)
    count_clicks_on(page, SUBMIT)
    adapter.fill(page, job, resolver({**APPROVED, "why": "Because", "salary": "5000"}))
    assert clicks(page) == 0


def test_fill_does_not_run_on_a_page_that_needs_a_human(adapter, page, job):
    page.set_content(fixture_html("ashby_thndr.html"))  # CAPTCHA widget still there
    missing = adapter.fill(page, job, resolver(APPROVED))
    assert missing and value_of(page, NAME) == ""


def test_submit_clicks_once_and_confirms_only_on_the_boards_own_message(adapter, page, job, monkeypatch):
    page.set_content(fixture_html("ashby_thndr.html"))
    page.evaluate(REMOVE_CAPTCHA)
    count_clicks_on(page, SUBMIT)
    page.evaluate(SUCCESS_ON_CLICK, SUBMIT)
    receipt = adapter.submit(page)
    assert clicks(page) == 1
    assert receipt.confirmed is True and "successfully submitted" in receipt.text


def test_submit_without_a_confirmation_is_not_confirmed(adapter, page, monkeypatch):
    monkeypatch.setattr(ashby, "CONFIRM_SECONDS", 1)
    page.set_content(fixture_html("ashby_thndr.html"))
    page.evaluate(REMOVE_CAPTCHA)
    count_clicks_on(page, SUBMIT)
    receipt = adapter.submit(page)
    assert clicks(page) == 1 and receipt.confirmed is False


def test_a_validation_error_after_the_click_is_not_a_confirmation(adapter, page, monkeypatch):
    monkeypatch.setattr(ashby, "CONFIRM_SECONDS", 1)
    page.set_content(fixture_html("ashby_thndr.html"))
    page.evaluate(REMOVE_CAPTCHA)
    page.evaluate("""(sel) => document.querySelector(sel).addEventListener('click', e => { e.preventDefault();
        document.querySelector('#form').insertAdjacentHTML('afterbegin', '<p class="ashby-application-form-field-error">Missing entry</p>'); })""", SUBMIT)
    assert adapter.submit(page).confirmed is False


def test_submit_never_clicks_on_a_page_that_needs_a_human(adapter, page):
    page.set_content(fixture_html("ashby_thndr.html"))
    count_clicks_on(page, SUBMIT)
    receipt = adapter.submit(page)
    assert clicks(page) == 0 and receipt == Receipt(False, receipt.text) and "human" in receipt.text.lower()


def test_submit_with_no_button_is_not_confirmed(adapter, page):
    page.set_content("<h1>Closed</h1>")
    assert adapter.submit(page).confirmed is False


def test_hostile_text_in_the_form_changes_nothing(adapter, page, job, monkeypatch):
    monkeypatch.setattr(ashby, "CONFIRM_SECONDS", 1)
    page.set_content(fixture_html("ashby_injection.html"))
    page.evaluate(REMOVE_CAPTCHA)
    count_clicks_on(page, SUBMIT)
    resolve = resolver(APPROVED)
    missing = adapter.fill(page, job, resolve)
    assert clicks(page) == 0
    assert [value_of(page, s) for s in (WHY, CURRENT, EXPECTED)] == ["", "", ""]
    assert labels(missing) == ["Why Thndr?", "Current Salary", "Expected Salary"]
    assert not any("SYSTEM NOTICE" in str(call) for call in resolve.calls)  # descriptions are never questions
    assert adapter.submit(page).confirmed is False  # the fake "successfully submitted" in the page is not the board's confirmation


def test_from_the_posting_page_it_opens_the_application_tab(adapter, page, job):
    overview = "https://jobs.ashbyhq.com/thndr/706a3396-c21e-4803-8648-f6e590e41b08"
    page.route(overview, lambda r: r.fulfill(content_type="text/html", body="<h1>Senior DevOps</h1><a href='/x'>Overview</a>"))
    page.route(THNDR, lambda r: r.fulfill(content_type="text/html", body=fixture_html("ashby_thndr.html")))
    page.goto(overview)
    assert adapter.needs_human(page)  # looked at the form, not the posting
    assert page.url == THNDR
