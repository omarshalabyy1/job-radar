"""Greenhouse application form (job-boards.greenhouse.io and boards.greenhouse.io, which serves the same React form).

Written against forms saved on 2026-11-08 (tests/fixtures/apply/greenhouse_*.html). What the form is: `form#application-form`; text inputs and
textareas with `label[for]`; dropdowns are react-select (`input.select__input[role=combobox]`, options in `#react-select-<id>-listbox`, some
load while typing: location, school); checkboxes sit in a `fieldset` with a `legend`; file inputs (`#resume`, `#cover_letter`) start an upload to
Greenhouse's bucket the moment a file is chosen, with a progress bar until it ends. Real pages also load an invisible reCAPTCHA Enterprise,
so `needs_human` is True on them (see human_check); an emailed 8-character security code may follow Submit.

Page text is data. The text of a label is only ever the *question* handed to `resolve`; no answer is taken from the page, and no label can make
the adapter click anything but the one submit button."""

from __future__ import annotations

import re

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from . import forms
from .base import Field, Receipt
from .human_check import needs_human as _needs_human
from ..queue import board_of

# One pass over the form, in page order: what each control is, what it asks, whether it is required, and (for groups) its options.
SCAN = """() => {
  const form = document.querySelector('form#application-form') || document.querySelector('form');
  if (!form) return [];
  const text = el => (el ? el.innerText || el.textContent : '') || '';
  const labelOf = el => { const l = el.id && form.querySelector(`label[for="${el.id}"]`); return l ? text(l) : (el.getAttribute('aria-label') || ''); };
  const out = [], seen = new Set();
  form.querySelectorAll('input, textarea').forEach(el => {
    const type = (el.getAttribute('type') || 'text').toLowerCase();
    if (['hidden', 'submit', 'button', 'image'].includes(type) || el.getAttribute('aria-hidden') === 'true' || !el.id) return;
    if (el.classList.contains('iti__search-input')) return;                      // the phone widget's own search box
    const req = el.getAttribute('aria-required') === 'true' || el.required;
    if (el.classList.contains('select__input')) {                                  // react-select
      out.push({id: el.id, kind: 'select', label: labelOf(el), required: req}); return;
    }
    if (type === 'file') {
      const grp = el.closest('[role=group]'); const lab = grp && grp.getAttribute('aria-labelledby');
      out.push({id: el.id, kind: 'file', label: text(lab ? document.getElementById(lab) : null) || labelOf(el),
                required: !!(grp && grp.getAttribute('aria-required') === 'true') || req}); return;
    }
    if (type === 'checkbox' || type === 'radio') {
      const key = type + ':' + (el.name || el.id); if (seen.has(key)) return; seen.add(key);
      const group = [...form.querySelectorAll(`input[type=${type}]`)].filter(i => (i.name || i.id) === (el.name || el.id));
      const fs = el.closest('fieldset'); const legend = fs && fs.querySelector('legend');
      const options = group.map(i => text(form.querySelector(`label[for="${i.id}"]`)).trim());
      out.push({id: el.id, kind: type === 'radio' ? 'radio' : 'checkbox', label: legend ? text(legend) : options[0],
                required: group.some(i => i.required || i.getAttribute('aria-required') === 'true') ||
                          !!(fs && (fs.getAttribute('aria-required') === 'true' || fs.querySelector('legend .required'))),
                options, ids: group.map(i => i.id)}); return;
    }
    out.push({id: el.id, kind: type === 'number' ? 'number' : 'text', label: labelOf(el),
              required: req || /\\*\\s*$/.test(labelOf(el))});
  });
  return out;
}"""

SETTLED = """() => {
  const MESSAGES = /please complete the recaptcha|8-character code|security code/i;
  const form = document.querySelector('form#application-form');
  const body = document.body ? document.body.innerText : '';
  return !form || MESSAGES.test(body);
}"""

RESULT = """() => {
  const form = document.querySelector('form#application-form');
  const body = document.body ? document.body.innerText : '';
  return {form: !!form, body: body.slice(0, 20000)};
}"""

RECEIVED = re.compile(r"we(?:'|’)ve received your application|thank you for applying to", re.I)  # Greenhouse's own words (locale confirmation.en)


def _id(i: str) -> str:
    return f'[id="{i}"]'


class Adapter:
    name = "greenhouse"
    SUBMIT_WAIT_MS = 30_000   # how long to wait for the page to answer a click on Submit
    UPLOAD_WAIT_MS = 30_000   # how long an upload's progress bar may stay
    TYPEAHEAD_WAIT_MS = 4_000  # how long a dropdown that loads while typing may take to list its options

    def can_handle(self, url: str) -> bool:
        return board_of(url) == self.name

    def needs_human(self, page) -> bool:
        return _needs_human(page)

    # -- fill --------------------------------------------------------------------------------------------------------------------

    def fill(self, page, job, resolve) -> list[Field]:
        forms.refuse_if_human(page)
        controls = page.evaluate(SCAN)
        for c in controls:
            c["label"] = forms.clean_label(c["label"])
        unanswered: list[tuple[int, Field]] = []
        # files first: an upload can trigger autofill on some boards, and every answer must be the last word on its field
        for index, c in sorted(enumerate(controls), key=lambda ic: ic[1]["kind"] != "file"):
            options: list[str] = []
            try:
                done, options = self._fill_one(page, job, resolve, c)
            except PlaywrightError:  # a control that cannot be driven is unanswered, never half-guessed
                done = False
            if not done and c["required"]:
                kind = {"radio": "select"}.get(c["kind"], c["kind"])
                unanswered.append((index, Field(c["label"], kind, tuple(options or c.get("options") or ()), True)))
        return [field for _, field in sorted(unanswered, key=lambda t: t[0])]  # in page order

    def _fill_one(self, page, job, resolve, c) -> tuple[bool, list[str]]:
        kind, label = c["kind"], c["label"]
        if kind == "file":
            return self._upload(page, job, c), []
        if kind in ("text", "number"):
            answer = forms.ask(resolve, label, kind)
            if answer is None:
                return False, []
            page.locator(_id(c["id"])).fill(answer)
            return True, []
        if kind == "select":
            return self._choose(page, resolve, c)
        options = c["options"]
        answer = forms.ask(resolve, label, "checkbox" if kind == "checkbox" else "select", options)
        if answer is None:
            return False, options
        box = page.locator(_id(c["ids"][options.index(answer)]))
        box.check()
        return box.is_checked(), options

    def _upload(self, page, job, c) -> bool:
        wanted = c["id"].lower() + " " + c["label"].lower()
        if "cover" in wanted:
            path = forms.existing_file(getattr(job, "cover_pdf", None))
        elif "resume" in wanted or re.search(r"\bcv\b", wanted):
            path = forms.existing_file(getattr(job, "cv_pdf", None))
        else:
            path = None  # a file we were not given: not ours to invent
        if path is None:
            return False
        group = page.locator(_id(c["id"])).locator("xpath=ancestor::*[contains(@class,'file-upload')][1]")
        page.locator(_id(c["id"])).set_input_files(path)
        page.wait_for_timeout(250)  # the progress bar appears a moment after the file is chosen
        try:
            group.locator("[role=progressbar]").wait_for(state="detached", timeout=self.UPLOAD_WAIT_MS)
        except PlaywrightTimeout:
            return False  # still uploading: not counted as done
        return True

    def _choose(self, page, resolve, c) -> tuple[bool, list[str]]:
        combo = page.locator(_id(c["id"]))
        listbox = page.locator(_id(f"react-select-{c['id']}-listbox"))
        options_of = lambda: [t.strip() for t in listbox.locator("[role=option]").all_inner_texts()]  # noqa: E731
        combo.click()
        options = options_of()
        if options:
            answer = forms.ask(resolve, c["label"], "select", options)
            if answer is None:
                combo.press("Escape")
                return False, options
        else:  # options that load while typing (location, school): type the approved text and take only an exact match
            answer = forms.ask(resolve, c["label"], "text")
            if answer is None:
                combo.press("Escape")
                return False, []
            combo.press_sequentially(answer, delay=15)
            try:
                listbox.locator("[role=option]").first.wait_for(timeout=self.TYPEAHEAD_WAIT_MS)
            except PlaywrightTimeout:
                pass
            options = options_of()
            match = [i for i, o in enumerate(options) if forms.norm(o) == forms.norm(answer)]
            if not match:
                combo.press("Control+A")
                combo.press("Backspace")
                combo.press("Escape")
                return False, options
            answer = options[match[0]]
        listbox.locator("[role=option]").nth(options.index(answer)).click()
        shown = combo.locator("xpath=ancestor::div[contains(@class,'select__container')][1]").locator(".select__single-value")
        return shown.count() == 1 and shown.inner_text().strip() == answer, options

    # -- submit ------------------------------------------------------------------------------------------------------------------

    def submit(self, page) -> Receipt:
        forms.refuse_if_human(page)
        button = page.locator("form#application-form button[type=submit]")
        if button.count() == 0:
            return Receipt(False, "NOT CONFIRMED: no submit button on the page")
        button.first.click()  # once; never retried here
        try:
            page.wait_for_function(SETTLED, timeout=self.SUBMIT_WAIT_MS)
        except PlaywrightTimeout:
            pass
        except PlaywrightError:  # the page navigated under the wait: read the new one
            try:
                page.wait_for_load_state("domcontentloaded", timeout=self.SUBMIT_WAIT_MS)
            except PlaywrightError:
                pass
        state = page.evaluate(RESULT)
        body = state["body"]
        if not state["form"]:  # the application form is gone: only then can the board's own words count
            line = next((ln.strip() for ln in body.splitlines() if RECEIVED.search(ln)), None)
            if line:
                return Receipt(True, line)
            return Receipt(False, "NOT CONFIRMED: the form is gone but the page has no confirmation text")
        for pattern, why in ((r"8-character code|security code", "Greenhouse asked for the emailed security code"),
                             (r"please complete the recaptcha", "Greenhouse asked for the reCAPTCHA again")):
            if re.search(pattern, body, re.I):
                return Receipt(False, f"NOT CONFIRMED: {why}")
        return Receipt(False, "NOT CONFIRMED: the application form is still showing")
