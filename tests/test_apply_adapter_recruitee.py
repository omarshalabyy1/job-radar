"""The Recruitee form adapter against two saved real forms (tests/fixtures/apply/recruitee_synapse_*.html: synapseanalytics.recruitee.com
/o/<job>/c/new, saved 2026-11-08) and one with hostile text added. Quirks of the board these pin: an hCaptcha checkbox sits above Send,
so the real forms are `needs_human`; the success, "already applied" and error panels are in the page all the time and only the active
one is the board's answer (hidden text is not a receipt); questions are radio groups under a legend, the privacy consent is a checkbox
whose label is the whole legal sentence; the phone box comes prefilled with a dial code."""

from dataclasses import replace

import pytest
from apply_form_helpers import browser, page  # noqa: F401  (fixtures)
from apply_page import clicks, count_clicks_on, fast_waits, fixture_html, job, labels, resolver, uploaded, value_of  # noqa: F401

from job_radar.apply import queue
from job_radar.apply.adapters import recruitee
from job_radar.apply.adapters.base import Field, Receipt

SUBMIT = '[data-testid="submit-application-form-button"]'
FORM_URL = "https://synapseanalytics.recruitee.com/o/senior-forward-deployed-engineer/c/new"
FORWARD, RISK = "recruitee_synapse_forward_deployed.html", "recruitee_synapse_risk_analyst.html"
APPROVED = {"full name": "Omar Shalaby", "email": "omar@example.com", "phone": "+201001234567", "military": "Finished",
            "python": "Yes", "django": "No", "privacy": "Yes", "hear": "LinkedIn"}
NAME, EMAIL, PHONE, CV, CONSENT = ('[name="candidate.name"]', '[name="candidate.email"]', '[name="candidate.phone"]',
                                   '[name="candidate.cv"]', '[name="candidate.agreements.0.consent"]')
REMOVE_CAPTCHA = """() => document.querySelectorAll('iframe, [id*="captcha" i], [class*="captcha" i]').forEach(e => e.remove())"""
# what the board's own script does after a good submit (not in the saved DOM): its success panel becomes the active one
SUCCESS_ON_CLICK = """(sel) => document.querySelector(sel).addEventListener('click', e => { e.preventDefault();
    const p = document.querySelector('[id$="-content-offer-application-success"]'); p.hidden = false; p.setAttribute('data-state', 'active');
    document.querySelector('[id$="-content-offer-application-form"]').hidden = true; })"""
ALREADY_ON_CLICK = SUCCESS_ON_CLICK.replace("application-success", "application-rate-limited")


def checked(page, name):
    return page.locator(f'input[name="{name}"]:checked').evaluate_all("els => els.map(e => e.value)")


@pytest.fixture
def adapter():
    return recruitee.Adapter()


def test_name_is_what_the_queue_calls_the_board(adapter):
    assert adapter.name == queue.board_of(FORM_URL) == "recruitee"


def test_can_handle_recruitee_careers_urls_only(adapter):
    assert adapter.can_handle(FORM_URL)
    assert adapter.can_handle("https://elmenus.recruitee.com/o/backend-engineer")
    assert not adapter.can_handle("https://apply.workable.com/acme/j/ABC/")
    assert not adapter.can_handle("https://recruitee.com.evil.example/o/x")
    assert not adapter.can_handle("https://evil.example/?next=acme.recruitee.com")


@pytest.mark.parametrize("name", [FORWARD, RISK])
def test_the_real_forms_carry_hcaptcha_so_they_need_a_human(adapter, page, name):
    page.set_content(fixture_html(name))
    assert adapter.needs_human(page) is True


@pytest.mark.parametrize("name", [FORWARD, RISK])
def test_without_the_captcha_widget_the_form_does_not_need_a_human(adapter, page, name):
    page.set_content(fixture_html(name))
    page.evaluate(REMOVE_CAPTCHA)
    assert adapter.needs_human(page) is False


def test_a_login_wall_or_a_verify_human_page_needs_a_human(adapter, page):
    page.set_content("<h1>Log in to apply</h1><input type=password>")
    assert adapter.needs_human(page)
    page.set_content("<title>Attention Required! | Cloudflare</title><h1>Verify you are human by completing the action below.</h1>")
    assert adapter.needs_human(page)


def test_a_page_with_no_form_is_an_error_not_ready_to_submit(adapter, page, job):
    page.set_content("<h1>This job is closed</h1>")
    with pytest.raises(RuntimeError, match="no application form"):
        adapter.fill(page, job, resolver(APPROVED))


def test_fill_answers_come_only_from_resolve(adapter, page, job):
    page.set_content(fixture_html(FORWARD))
    page.evaluate(REMOVE_CAPTCHA)
    resolve = resolver(APPROVED)
    missing = adapter.fill(page, job, resolve)
    assert missing == []  # everything has an approved answer: ready
    assert value_of(page, NAME) == "Omar Shalaby" and value_of(page, EMAIL) == "omar@example.com"
    assert value_of(page, PHONE) == "+201001234567"
    assert checked(page, "candidate.openQuestionAnswers.7547490.content") == ["Finished"]
    assert checked(page, "candidate.openQuestionAnswers.7547491.flag") == ["true"]    # Python: Yes
    assert checked(page, "candidate.openQuestionAnswers.7547492.flag") == ["false"]   # Django: No
    assert page.locator(CONSENT).is_checked()
    asked = {q: (t, o) for q, t, o in resolve.calls}
    assert asked["Military Service"] == ("select", ["Finished", "Exempted", "Postponed", "In Progress", "Not Applicable (for females)"])
    assert asked["Do you have any experience working in Python?"] == ("select", ["Yes", "No"])
    assert asked["Full name"] == ("text", None)
    assert next(t for q, (t, o) in asked.items() if q.startswith("I hereby confirm")) == "checkbox"


def test_what_has_no_approved_answer_stays_empty_and_is_returned(adapter, page, job):
    page.set_content(fixture_html(FORWARD))
    page.evaluate(REMOVE_CAPTCHA)
    missing = adapter.fill(page, job, resolver({"full name": "Omar Shalaby", "email": "omar@example.com"}))
    assert value_of(page, PHONE) == "+20"  # the prefilled dial code, not an answer
    assert checked(page, "candidate.openQuestionAnswers.7547490.content") == []
    assert not page.locator(CONSENT).is_checked()
    assert labels(missing)[:4] == ["Phone number", "Military Service", "Do you have any experience working in Python?",
                                   "Do you have any experience working in Django?"]
    assert missing[1] == Field("Military Service", "select",
                               ("Finished", "Exempted", "Postponed", "In Progress", "Not Applicable (for females)"), True)
    assert labels(missing)[4].startswith("I hereby confirm")


def test_an_answer_that_is_not_one_of_the_options_is_not_used(adapter, page, job):
    page.set_content(fixture_html(FORWARD))
    page.evaluate(REMOVE_CAPTCHA)
    answers = {**APPROVED, "military": "Not sure"}
    missing = adapter.fill(page, job, resolver(answers))
    assert labels(missing) == ["Military Service"] and checked(page, "candidate.openQuestionAnswers.7547490.content") == []


def test_an_optional_question_with_no_answer_is_not_returned(adapter, page, job):
    page.set_content(fixture_html(RISK))
    page.evaluate(REMOVE_CAPTCHA)
    missing = adapter.fill(page, job, resolver({k: v for k, v in APPROVED.items() if k != "hear"}))
    assert "How did your hear about us?" not in labels(missing)  # its radios are not required


def test_fill_uploads_the_tailored_cv(adapter, page, job):
    page.set_content(fixture_html(FORWARD))
    page.evaluate(REMOVE_CAPTCHA)
    adapter.fill(page, job, resolver(APPROVED))
    assert uploaded(page, CV) == ["Omar Shalaby Acme CV.pdf"]


def test_a_missing_cv_file_is_returned_not_skipped(adapter, page, job, tmp_path):
    page.set_content(fixture_html(FORWARD))
    page.evaluate(REMOVE_CAPTCHA)
    missing = adapter.fill(page, replace(job, cv_pdf=None), resolver(APPROVED))
    assert [(f.label, f.kind) for f in missing] == [("CV or resume", "file")]
    assert uploaded(page, CV) == []


def test_the_cover_letter_goes_only_into_a_cover_letter_field(adapter, page, job):
    page.set_content(fixture_html(FORWARD))
    page.evaluate(REMOVE_CAPTCHA)
    page.evaluate("""() => document.querySelector('[name="candidate.cv"]').closest('section').insertAdjacentHTML('afterend',
        '<section><label for="cl">Cover letter</label><input type="file" id="cl" name="candidate.coverLetter">' +
        '<label for="id">Upload your ID card</label><input type="file" id="id" name="candidate.idCard" required></section>')""")
    missing = adapter.fill(page, job, resolver(APPROVED))
    assert uploaded(page, "#cl") == ["Omar Shalaby Acme Cover Letter.pdf"] and uploaded(page, CV) == ["Omar Shalaby Acme CV.pdf"]
    assert uploaded(page, "#id") == [] and [(f.label, f.kind) for f in missing] == [("Upload your ID card", "file")]


def test_a_control_it_cannot_read_is_returned_as_required_never_guessed(adapter, page, job):
    page.set_content(fixture_html(FORWARD))
    page.evaluate(REMOVE_CAPTCHA)
    page.evaluate("""() => document.querySelector('[data-testid="segment-open-questions"] fieldset').insertAdjacentHTML('beforeend',
        '<div><label id="l1">Preferred city</label><button type="button" role="combobox" aria-labelledby="l1">Choose</button></div>')""")
    missing = adapter.fill(page, job, resolver(APPROVED))
    assert labels(missing) == ["Preferred city"]  # and the phone prefix box (a combobox too) is not one of them


def test_fill_never_clicks_send(adapter, page, job):
    page.set_content(fixture_html(FORWARD))
    page.evaluate(REMOVE_CAPTCHA)
    count_clicks_on(page, SUBMIT)
    adapter.fill(page, job, resolver(APPROVED))
    assert clicks(page) == 0


def test_fill_refuses_a_page_that_needs_a_human(adapter, page, job):
    page.set_content(fixture_html(FORWARD))  # hCaptcha still there
    with pytest.raises(RuntimeError, match="needs a human"):
        adapter.fill(page, job, resolver(APPROVED))
    assert value_of(page, NAME) == ""


def test_submit_clicks_once_and_confirms_only_on_the_active_success_panel(adapter, page, monkeypatch):
    page.set_content(fixture_html(FORWARD))
    page.evaluate(REMOVE_CAPTCHA)
    count_clicks_on(page, SUBMIT)
    page.evaluate(SUCCESS_ON_CLICK, SUBMIT)
    receipt = adapter.submit(page)
    assert clicks(page) == 1
    assert receipt.confirmed is True and "Thank you for applying!" in receipt.text and "successfully submitted" in receipt.text


def test_submit_without_a_confirmation_is_not_confirmed(adapter, page, monkeypatch):
    monkeypatch.setattr(recruitee, "CONFIRM_SECONDS", 1)
    page.set_content(fixture_html(FORWARD))  # the success text is in the page, hidden: not a receipt
    page.evaluate(REMOVE_CAPTCHA)
    count_clicks_on(page, SUBMIT)
    receipt = adapter.submit(page)
    assert clicks(page) == 1 and receipt.confirmed is False


def test_already_applied_is_not_a_confirmation(adapter, page, monkeypatch):
    monkeypatch.setattr(recruitee, "CONFIRM_SECONDS", 1)
    page.set_content(fixture_html(FORWARD))
    page.evaluate(REMOVE_CAPTCHA)
    page.evaluate(ALREADY_ON_CLICK, SUBMIT)
    assert adapter.submit(page).confirmed is False


def test_submit_never_clicks_on_a_page_that_needs_a_human(adapter, page):
    page.set_content(fixture_html(FORWARD))
    count_clicks_on(page, SUBMIT)
    receipt = adapter.submit(page)
    assert clicks(page) == 0 and receipt == Receipt(False, receipt.text) and receipt.text.startswith("NOT CONFIRMED") and "human" in receipt.text


def test_submit_with_no_button_is_not_confirmed(adapter, page):
    page.set_content("<h1>Closed</h1>")
    assert adapter.submit(page).confirmed is False


def test_hostile_text_in_the_form_changes_nothing(adapter, page, job, monkeypatch):
    monkeypatch.setattr(recruitee, "CONFIRM_SECONDS", 1)
    page.set_content(fixture_html("recruitee_injection.html"))
    page.evaluate(REMOVE_CAPTCHA)
    count_clicks_on(page, SUBMIT)
    resolve = resolver({"full name": "Omar Shalaby", "email": "omar@example.com"})
    missing = adapter.fill(page, job, resolve)
    assert clicks(page) == 0
    assert checked(page, "candidate.openQuestionAnswers.7547490.content") == []
    assert checked(page, "candidate.openQuestionAnswers.7547491.flag") == [] and not page.locator(CONSENT).is_checked()
    assert labels(missing)[:2] == ["Phone number", "Military Service"]
    assert not any("SYSTEM NOTICE" in str(call) for call in resolve.calls)  # descriptions are never questions
    assert adapter.submit(page).confirmed is False  # the fake "successfully submitted" in the page is not the board's panel


def test_from_the_posting_page_it_opens_the_application_form(adapter, page):
    overview = "https://synapseanalytics.recruitee.com/o/senior-forward-deployed-engineer"
    page.route(overview, lambda r: r.fulfill(content_type="text/html", body="<h1>Senior Forward Deployed Engineer</h1><a href='x'>Apply</a>"))
    page.route(FORM_URL, lambda r: r.fulfill(content_type="text/html", body=fixture_html(FORWARD)))
    page.goto(overview)
    assert adapter.needs_human(page)  # looked at the form, not the posting
    assert page.url == FORM_URL
