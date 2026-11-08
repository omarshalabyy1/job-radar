"""Recruitee (<company>.recruitee.com): the application form at /o/<job>/c/new (docs/superpowers/plans/2026-11-08-auto-apply.md, Task 4).

Quirks, from two saved real forms (tests/fixtures/apply/recruitee_synapse_*.html):
- An hCaptcha checkbox ("Widget containing checkbox for hCaptcha security challenge") sits above Send, so every real form is
  `needs_human` today.
- One form `#offer-application-form`; the page keeps the success, "already applied" and details panels in the DOM as hidden tabs, so
  their text is always there: the confirmation is the success panel being the *active* one, never text found in the page.
- Questions are radio groups under a `<legend>` (Yes/No questions have values true/false), the privacy consent is a required checkbox whose
  label is the whole legal sentence, the CV is `candidate.cv`, the phone box comes prefilled with a dial code (+20 for Egypt) that is not
  an answer, and a country-code dropdown (a combobox button) beside it is not a question."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from . import _ats
from .base import Field, Receipt

CONFIRM_SECONDS = 20
FORM = "form#offer-application-form"
SUBMIT = 'button[data-testid="submit-application-form-button"]'
_CONFIRMATION = """() => {
  const panel = document.querySelector('[id$="-content-offer-application-success"]');
  if (!panel || panel.hidden || panel.getAttribute('data-state') !== 'active') return null;
  const text = panel.innerText.replace(/\\s+/g, ' ').trim();
  return text || 'application submitted';
}"""


def _application_url(url: str) -> str | None:
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    if path.endswith("/c/new") or "/o/" not in path:
        return None  # already there, or not a single job's page
    return urlunsplit(parts._replace(path=path + "/c/new"))


class Adapter:
    name = "recruitee"

    def can_handle(self, url: str) -> bool:
        return _ats.on_host(url, (), ("recruitee.com",)) and _ats.host_of(url) != "recruitee.com"

    def _ready(self, page) -> bool:
        return _ats.open_form(page, FORM, _application_url)

    def needs_human(self, page) -> bool:
        self._ready(page)  # from a job's page this opens the form, so its hCaptcha is the one looked for
        return bool(_ats.human_check(page))

    def fill(self, page, job, resolve) -> list[Field]:
        self._ready(page)
        if reason := _ats.human_check(page):
            return [Field(f"needs a human: {reason}", "text", (), True)]
        return _ats.fill_form(page, job, resolve, FORM)

    def submit(self, page) -> Receipt:
        if not self._ready(page):
            return Receipt(False, "no application form on the page")
        if reason := _ats.human_check(page):
            return Receipt(False, f"needs a human: {reason}")
        button = page.locator(SUBMIT)
        if button.count() != 1 or not button.is_enabled():
            return Receipt(False, "no single enabled submit button")
        button.click()
        return _ats.wait_for_receipt(page, lambda p: p.evaluate(_CONFIRMATION), CONFIRM_SECONDS)
