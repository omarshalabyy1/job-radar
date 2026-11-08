"""Ashby (jobs.ashbyhq.com): the application form at <posting>/application (docs/superpowers/plans/2026-11-08-auto-apply.md, Task 4).

Quirks, from two saved real forms (tests/fixtures/apply/ashby_*.html):
- The page loads Google reCAPTCHA Enterprise (a badge and iframes, no checkbox), so every real form is `needs_human` today.
- An "Autofill from resume" upload sits above the form; it is never touched (it would parse the CV and fill fields we did not choose).
  The form itself is `.ashby-application-form-container`; each question is an `.ashby-application-form-field-entry` with a
  label[for] and one control; the fields are all text, textarea, file today.
- A single-page app: the form is drawn after the page loads, and a posting's overview page and its /application tab are one app.
- The board's confirmation replaces the form with a message; it is not in a saved page (we never submit one), so it is read as: no
  question and no submit button left, and the board's "successfully submitted" wording in what is left."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from . import _ats
from .base import Field, Receipt

CONFIRM_SECONDS = 20
FORM = ".ashby-application-form-container"
SUBMIT = "button.ashby-application-form-submit-button"
ENTRY = ".ashby-application-form-field-entry"
AUTOFILL = ".ashby-application-form-autofill-pane"
_CONFIRMATION = """() => {
  if (document.querySelector('.ashby-application-form-field-entry, button.ashby-application-form-submit-button')) return null;
  const box = document.querySelector('.ashby-application-form-success-container') || document.querySelector('#form') || document.body;
  const text = (box ? box.innerText : '').replace(/\\s+/g, ' ').trim();
  return /successfully submitted|application (has been )?(received|submitted)|thank you for applying/i.test(text) ? text.slice(0, 300) : null;
}"""


def _application_url(url: str) -> str | None:
    parts = urlsplit(url)
    if parts.path.rstrip("/").endswith("/application") or len([p for p in parts.path.split("/") if p]) < 2:
        return None  # already there, or a company's job list: no single form to go to
    return urlunsplit(parts._replace(path=parts.path.rstrip("/") + "/application"))


class Adapter:
    name = "ashby"

    def can_handle(self, url: str) -> bool:
        return _ats.on_host(url, ("jobs.ashbyhq.com",))

    def _ready(self, page) -> bool:
        return _ats.open_form(page, FORM, _application_url)

    def needs_human(self, page) -> bool:
        self._ready(page)  # from a posting's overview this opens the form, so its CAPTCHA is the one looked for
        return _ats.needs_human(page)

    def fill(self, page, job, resolve) -> list[Field]:
        self._ready(page)
        _ats.refuse_if_human(page)
        return _ats.fill_form(page, job, resolve, FORM, skip=AUTOFILL, entry=ENTRY)

    def submit(self, page) -> Receipt:
        if not self._ready(page):
            return Receipt(False, "NOT CONFIRMED: no application form on the page")
        if _ats.needs_human(page):
            return Receipt(False, "NOT CONFIRMED: needs a human (CAPTCHA, login wall or blocked page); nothing was clicked")
        button = page.locator(SUBMIT)
        if button.count() != 1 or not button.is_enabled():
            return Receipt(False, "NOT CONFIRMED: no single enabled submit button on the page")
        button.click()
        return _ats.wait_for_receipt(page, lambda p: p.evaluate(_CONFIRMATION), CONFIRM_SECONDS)
