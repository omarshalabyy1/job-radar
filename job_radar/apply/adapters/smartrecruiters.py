"""SmartRecruiters (jobs.smartrecruiters.com): the application form behind a posting's "I'm interested" button
(docs/superpowers/plans/2026-11-08-auto-apply.md, Task 4).

NOT VERIFIED AGAINST A REAL FORM. SmartRecruiters turned every automated request for the form away with HTTP 403 on 2026-11-08 (its
posting API answers, the form page at /oneclick-ui/company/<Company>/publication/<posting uuid> does not), so no real form was saved and
none was worked around. This adapter is therefore built to fail closed: it fills only a standard form (labelled inputs in one `<form>`, no
shadow DOM) that has exactly one button saying Submit; a multi-step form ("Next"), a form drawn inside web components, a blocked or
verify-human page and a CAPTCHA all stop it (`needs_human`, or an error from `fill`), never a guess. Keep `smartrecruiters` out of
`apply.boards` until one real form has been seen in a dry run (tests/fixtures/apply/smartrecruiters_reconstructed.html is a stand-in
written by hand, not a saved page). The confirmation is the generic one: the form gone and the board's "application submitted" wording."""

from __future__ import annotations

from . import _ats
from .base import Field, Receipt

CONFIRM_SECONDS = 20
FORM = "form:has(input[type=email]), form:has(input[name*=email i])"
OPENER = r"^\s*(i'?m interested|apply( now)?)\s*$"


class Adapter:
    name = "smartrecruiters"

    def can_handle(self, url: str) -> bool:
        return _ats.on_host(url, ("jobs.smartrecruiters.com", "careers.smartrecruiters.com"))

    def _ready(self, page) -> bool:
        return _ats.open_form(page, FORM, opener=OPENER)

    def needs_human(self, page) -> bool:
        self._ready(page)  # from a posting this presses "I'm interested", so the form's own CAPTCHA is the one looked for
        return _ats.needs_human(page)

    def fill(self, page, job, resolve) -> list[Field]:
        self._ready(page)
        _ats.refuse_if_human(page)
        _ats.require_form_on_page(page, FORM)
        if _ats.single_submit(page, FORM) is None:
            raise RuntimeError("no single Submit button in the form (a multi-step form is not supported): not ready to submit")
        return _ats.fill_form(page, job, resolve, FORM)

    def submit(self, page) -> Receipt:
        if not self._ready(page):
            return Receipt(False, "NOT CONFIRMED: no application form on the page")
        if _ats.needs_human(page):
            return Receipt(False, "NOT CONFIRMED: needs a human (CAPTCHA, login wall or blocked page); nothing was clicked")
        button = _ats.single_submit(page, FORM)
        if button is None or not button.is_enabled():
            return Receipt(False, "NOT CONFIRMED: no single enabled Submit button on the page")
        button.click()
        return _ats.wait_for_receipt(page, lambda p: p.evaluate(_ats.GENERIC_CONFIRMATION, FORM), CONFIRM_SECONDS)
