"""The Workable form adapter. NO REAL FORM WAS AVAILABLE: Workable's Cloudflare answered the sandbox's requests with "unknown error" and then HTTP 429 and
a challenge (2026-11-08), so these tests run against tests/fixtures/apply/workable_reconstructed.html, a plain form written by hand
(its first line says so). They pin the adapter's rules (human checks, answers only from resolve, uploads, one click, its own confirmation,
failing closed on a form it does not understand), not the board's real markup; a saved real form must replace the stand-in before the board
joins `apply.boards`."""

from dataclasses import replace

import pytest
from apply_form_helpers import browser, page  # noqa: F401  (fixtures)
from apply_page import clicks, count_clicks_on, fast_waits, fixture_html, job, labels, resolver, uploaded, value_of  # noqa: F401

from job_radar.apply import queue
from job_radar.apply.adapters import workable
from job_radar.apply.adapters.base import Field, Receipt

FORM = "workable_reconstructed.html"
POSTING = "https://apply.workable.com/acme/j/ABC123DEF4/"
APPLY = "https://apply.workable.com/acme/j/ABC123DEF4/apply/"
SUBMIT = "form#application button[type=submit]"
APPROVED = {"first name": "Omar", "last name": "Shalaby", "email": "omar@example.com", "phone": "+201001234567",
            "authorised": "Yes", "notice": "1 month", "privacy": "Yes"}
# what the board's script does after a good submit: the form is replaced by its thank-you message
THANKS_ON_CLICK = """(sel) => document.querySelector(sel).addEventListener('click', e => { e.preventDefault();
    document.querySelector('form#application').replaceWith(Object.assign(document.createElement('p'),
        {textContent: 'Thank you for applying! Your application has been submitted.'})); })"""
ADD_CAPTCHA = """() => document.querySelector('form#application').insertAdjacentHTML('afterbegin', '<div class="g-recaptcha" data-sitekey="k"></div>')"""


def checked(page, name):
    return page.locator(f'input[name="{name}"]:checked').evaluate_all("els => els.map(e => e.value)")


@pytest.fixture
def adapter():
    return workable.Adapter()


def test_name_is_what_the_queue_calls_the_board(adapter):
    assert adapter.name == queue.board_of(POSTING) == "workable"


def test_can_handle_workable_urls_only(adapter):
    assert adapter.can_handle(POSTING)
    assert adapter.can_handle(APPLY)
    assert not adapter.can_handle("https://jobs.smartrecruiters.com/Acme/1-x")
    assert not adapter.can_handle("https://apply.workable.com.evil.example/x")
    assert not adapter.can_handle("https://evil.example/?next=apply.workable.com")


def test_the_cloudflare_429_page_that_turned_us_away_needs_a_human(adapter, page):
    page.set_content(fixture_html("workable_blocked.html"))
    assert adapter.needs_human(page) is True


def test_a_captcha_widget_needs_a_human_and_a_plain_form_does_not(adapter, page):
    page.set_content(fixture_html(FORM))
    assert adapter.needs_human(page) is False
    page.evaluate(ADD_CAPTCHA)
    assert adapter.needs_human(page) is True


def test_a_login_wall_or_a_verify_human_page_needs_a_human(adapter, page):
    page.set_content("<h1>Sign in to apply</h1><form><input name=email><input type=password></form>")
    assert adapter.needs_human(page)
    page.set_content("<h1>Verify you are human</h1><p>Checking your browser.</p>")
    assert adapter.needs_human(page)


def test_a_page_with_no_form_is_an_error_not_ready_to_submit(adapter, page, job):
    page.set_content("<h1>This job is no longer available</h1>")
    with pytest.raises(RuntimeError, match="no application form"):
        adapter.fill(page, job, resolver(APPROVED))


def test_fill_answers_come_only_from_resolve(adapter, page, job):
    page.set_content(fixture_html(FORM))
    resolve = resolver(APPROVED)
    assert adapter.fill(page, job, resolve) == []  # everything required has an approved answer: ready
    assert [value_of(page, s) for s in ("#firstName", "#lastName", "#email", "#phone")] == ["Omar", "Shalaby", "omar@example.com", "+201001234567"]
    assert checked(page, "auth") == ["yes"] and value_of(page, "#notice") == "1 month" and page.locator("#consent").is_checked()
    assert value_of(page, "#why") == ""  # optional, no approved answer: left empty
    asked = {q: (t, o) for q, t, o in resolve.calls}
    assert asked["Are you legally authorised to work in Egypt?"] == ("select", ["Yes", "No"])
    assert asked["What is your notice period?"] == ("select", ["Immediately", "1 month", "2 months"])
    assert asked["First name"] == ("text", None) and asked["I agree to the privacy policy"] == ("checkbox", None)


def test_what_has_no_approved_answer_stays_empty_and_is_returned(adapter, page, job):
    page.set_content(fixture_html(FORM))
    missing = adapter.fill(page, job, resolver({"first name": "Omar", "email": "omar@example.com"}))
    assert labels(missing) == ["Last name", "Are you legally authorised to work in Egypt?", "What is your notice period?", "I agree to the privacy policy"]
    assert missing[1] == Field("Are you legally authorised to work in Egypt?", "select", ("Yes", "No"), True)
    assert value_of(page, "#lastName") == "" and checked(page, "auth") == [] and not page.locator("#consent").is_checked()


def test_an_answer_that_is_not_one_of_the_options_is_not_used(adapter, page, job):
    page.set_content(fixture_html(FORM))
    missing = adapter.fill(page, job, resolver({**APPROVED, "notice": "Next week"}))
    assert labels(missing) == ["What is your notice period?"] and value_of(page, "#notice") == ""


def test_fill_uploads_the_cv_and_the_cover_letter_each_into_its_own_field(adapter, page, job):
    page.set_content(fixture_html(FORM))
    adapter.fill(page, job, resolver(APPROVED))
    assert uploaded(page, "#resume") == ["Omar Shalaby Acme CV.pdf"]
    assert uploaded(page, "#cover") == ["Omar Shalaby Acme Cover Letter.pdf"]


def test_a_missing_cv_file_is_returned_and_an_unknown_upload_is_left_alone(adapter, page, job):
    page.set_content(fixture_html(FORM))
    page.evaluate("""() => document.querySelector('#cover').insertAdjacentHTML('afterend',
        '<label for="idcard">Upload your ID card</label><input type="file" id="idcard" required>')""")
    missing = adapter.fill(page, replace(job, cv_pdf=None), resolver(APPROVED))
    assert [(f.label, f.kind) for f in missing] == [("Resume", "file"), ("Upload your ID card", "file")]
    assert uploaded(page, "#resume") == [] and uploaded(page, "#idcard") == []


def test_a_control_it_cannot_read_is_returned_as_required_never_guessed(adapter, page, job):
    page.set_content(fixture_html(FORM))
    page.evaluate("""() => document.querySelector('#why').insertAdjacentHTML('afterend',
        '<span id="l9">Preferred city</span><div role="combobox" aria-labelledby="l9" tabindex="0">Choose</div>')""")
    assert labels(adapter.fill(page, job, resolver(APPROVED))) == ["Preferred city"]


def test_fill_never_clicks_submit(adapter, page, job):
    page.set_content(fixture_html(FORM))
    count_clicks_on(page, SUBMIT)
    adapter.fill(page, job, resolver(APPROVED))
    assert clicks(page) == 0


def test_fill_refuses_a_page_that_needs_a_human(adapter, page, job):
    page.set_content(fixture_html(FORM))
    page.evaluate(ADD_CAPTCHA)
    with pytest.raises(RuntimeError, match="needs a human"):
        adapter.fill(page, job, resolver(APPROVED))
    assert value_of(page, "#firstName") == ""


def test_a_multi_step_form_fails_closed(adapter, page, job):
    page.set_content(fixture_html(FORM).replace(">Submit</button>", ">Next</button>"))
    with pytest.raises(RuntimeError, match="Submit"):
        adapter.fill(page, job, resolver(APPROVED))
    assert value_of(page, "#firstName") == ""


def test_two_submit_buttons_fail_closed(adapter, page, job):
    page.set_content(fixture_html(FORM).replace('<button type="submit">Submit</button>', '<button type="submit">Submit</button><button type="submit">Submit and apply</button>'))
    with pytest.raises(RuntimeError, match="Submit"):
        adapter.fill(page, job, resolver(APPROVED))


def test_a_form_drawn_inside_web_components_fails_closed(adapter, page, job):
    page.set_content("<main><div id=host></div></main>")
    page.evaluate("""() => { const r = document.querySelector('#host').attachShadow({mode: 'open'});
        r.innerHTML = '<form><label for=e>Email</label><input id=e type=email><button type=submit>Submit</button></form>'; }""")
    with pytest.raises(RuntimeError, match="no application form"):
        adapter.fill(page, job, resolver(APPROVED))


def test_submit_clicks_once_and_confirms_only_on_the_boards_thank_you(adapter, page):
    page.set_content(fixture_html(FORM))
    count_clicks_on(page, SUBMIT)
    page.evaluate(THANKS_ON_CLICK, SUBMIT)
    receipt = adapter.submit(page)
    assert clicks(page) == 1
    assert receipt.confirmed is True and "Thank you for applying" in receipt.text


def test_submit_without_a_confirmation_is_not_confirmed(adapter, page, monkeypatch):
    monkeypatch.setattr(workable, "CONFIRM_SECONDS", 1)
    page.set_content(fixture_html(FORM))
    count_clicks_on(page, SUBMIT)
    receipt = adapter.submit(page)
    assert clicks(page) == 1 and receipt.confirmed is False and receipt.text.startswith("NOT CONFIRMED")


def test_submit_never_clicks_on_a_page_that_needs_a_human(adapter, page):
    page.set_content(fixture_html(FORM))
    page.evaluate(ADD_CAPTCHA)
    count_clicks_on(page, SUBMIT)
    receipt = adapter.submit(page)
    assert clicks(page) == 0 and receipt == Receipt(False, receipt.text) and "human" in receipt.text


def test_submit_with_no_form_or_no_single_button_is_not_confirmed(adapter, page):
    page.set_content("<h1>Closed</h1>")
    assert adapter.submit(page).confirmed is False
    page.set_content(fixture_html(FORM).replace(">Submit</button>", ">Next</button>"))
    count_clicks_on(page, "button")
    assert adapter.submit(page).confirmed is False and clicks(page) == 0


def test_hostile_text_in_the_form_changes_nothing(adapter, page, job, monkeypatch):
    monkeypatch.setattr(workable, "CONFIRM_SECONDS", 1)
    page.set_content(fixture_html("workable_injection.html"))
    count_clicks_on(page, SUBMIT)
    resolve = resolver({"first name": "Omar", "email": "omar@example.com"})
    missing = adapter.fill(page, job, resolve)
    assert clicks(page) == 0 and checked(page, "auth") == [] and not page.locator("#consent").is_checked()
    assert labels(missing)[0] == "Last name" and "What is your notice period?" in labels(missing)
    assert adapter.submit(page).confirmed is False  # the fake "Thank you for applying" in the page is not the board's: the form is still there


def test_from_the_posting_page_it_opens_the_apply_page(adapter, page):
    page.route(POSTING, lambda r: r.fulfill(content_type="text/html", body="<main><h1>Senior Data Engineer</h1><a href='apply/'>Apply for this job</a></main>"))
    page.route(APPLY, lambda r: r.fulfill(content_type="text/html", body=fixture_html(FORM)))
    page.goto(POSTING)
    assert adapter.needs_human(page) is False  # it looked at the form, not the posting
    assert page.url == APPLY and page.locator("form#application").count() == 1


def test_a_company_page_is_not_a_form_to_go_to(adapter, page, job):
    page.route("https://apply.workable.com/acme/", lambda r: r.fulfill(content_type="text/html", body="<main><h1>Openings</h1></main>"))
    page.goto("https://apply.workable.com/acme/")
    with pytest.raises(RuntimeError, match="no application form"):
        adapter.fill(page, job, resolver(APPROVED))
    assert page.url == "https://apply.workable.com/acme/"
