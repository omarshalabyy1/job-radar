"""Lever application form (jobs.lever.co/<company>/<id>/apply).

Written against forms saved on 2026-11-08 (tests/fixtures/apply/lever_*.html). What the form is: `form#application-form` of
`li.application-question` rows (label in `.application-label`, required marker `.required`); the resume input (`name=resume`) triggers Lever's
resume parser, which overwrites name, email and other fields, so the upload comes first and every answer after it; custom questions are
`cards[...]` inputs (radio and checkbox groups with `.application-answer-alternative` labels, selects, textareas, a cover-letter file input);
"Current location" is a typeahead that must be picked from its list (the hidden `selectedLocation` is then filled). Every real page also has an
hCaptcha that runs when Submit is clicked, so `needs_human` is True on them (see human_check).

Page text is data: a label is only ever the question handed to `resolve`."""

from __future__ import annotations

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from . import forms
from .base import Field, Receipt
from .human_check import needs_human as _needs_human
from ..queue import board_of

SCAN = """() => {
  const form = document.querySelector('form#application-form');
  if (!form) return [];
  const text = el => (el ? el.innerText || el.textContent : '') || '';
  const out = [];
  [...form.querySelectorAll('li.application-question')].forEach((li, index) => {
    if (li.parentElement.closest('li.application-question')) return;          // a nested row belongs to its parent
    const labelEl = li.querySelector('.application-label .text') || li.querySelector('.application-label');
    const required = !!li.querySelector('.application-label .required') || !!li.querySelector('[required]');
    li.setAttribute('data-lever-q', index);  // so Python can find this row again
    const label = text(labelEl);
    const pick = sel => [...li.querySelectorAll(sel)];
    const file = pick('input[type=file]')[0];
    if (file) { out.push({row: index, kind: 'file', label, required, name: file.name}); return; }
    const select = pick('select')[0];
    if (select) { out.push({row: index, kind: 'select', label, required,
                            options: [...select.options].filter(o => o.value !== '' && !o.disabled).map(o => o.text.trim())}); return; }
    const area = pick('textarea')[0];
    if (area) { out.push({row: index, kind: 'text', label, required}); return; }
    const choices = pick('input[type=radio], input[type=checkbox]').filter(i => i.name);
    if (choices.length) {
      const radio = choices[0].type === 'radio';
      const texts = choices.map(i => text(i.closest('label')).trim());
      out.push({row: index, kind: radio ? 'radio' : 'checkbox', label: label || texts[0], required, options: texts}); return;
    }
    if (li.querySelector('input[name=location]')) { out.push({row: index, kind: 'location', label, required}); return; }
    const input = pick('input').find(i => ['text', 'email', 'tel', 'url', 'number'].includes((i.getAttribute('type') || 'text').toLowerCase())
                                           && i.name !== 'selectedLocation' && i.offsetParent !== null);
    if (input) out.push({row: index, kind: 'text', label, required});
  });
  return out;
}"""

RESULT = """() => {
  const form = document.querySelector('form#application-form');
  const ok = document.querySelector('[data-qa=msg-submit-success]');
  return {form: !!form, ok: ok && !ok.closest('form') ? (ok.innerText || '').trim() : '',
          body: (document.body ? document.body.innerText : '').slice(0, 20000)};
}"""

SETTLED = """() => !document.querySelector('form#application-form') || !!document.querySelector('[data-qa=msg-submit-success]')"""


def _row(index: int) -> str:
    return f'li[data-lever-q="{index}"]'


class Adapter:
    name = "lever"
    SUBMIT_WAIT_MS = 30_000        # how long to wait for the page to answer a click on Submit
    RESUME_PARSE_WAIT_MS = 20_000  # how long Lever's resume parser may take before answers are filled anyway
    TYPEAHEAD_WAIT_MS = 4_000      # how long the location list may take

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
        # files first: the resume parser rewrites other fields, so every answer is typed after it and is the last word
        for index, c in sorted(enumerate(controls), key=lambda ic: ic[1]["kind"] != "file"):
            options: list[str] = []
            try:
                done, options = self._fill_one(page, job, resolve, c)
            except PlaywrightError:  # a control that cannot be driven is unanswered, never half-guessed
                done = False
            if not done and c["required"]:
                kind = {"radio": "select", "location": "text"}.get(c["kind"], c["kind"])
                unanswered.append((index, Field(c["label"], kind, tuple(options or c.get("options") or ()), True)))
        return [field for _, field in sorted(unanswered, key=lambda t: t[0])]  # in page order

    def _fill_one(self, page, job, resolve, c) -> tuple[bool, list[str]]:
        kind, label, row = c["kind"], c["label"], page.locator(_row(c["row"]))
        if kind == "file":
            return self._upload(page, job, c, row), []
        if kind == "location":
            return self._locate(page, resolve, c, row), []
        if kind == "text":
            answer = forms.ask(resolve, label, "text")
            if answer is None:
                return False, []
            row.locator("textarea, input:not([type=hidden])").first.fill(answer)
            return True, []
        options = c["options"]
        answer = forms.ask(resolve, label, "checkbox" if kind == "checkbox" else "select", options)
        if answer is None:
            return False, options
        if kind == "select":
            row.locator("select").select_option(label=answer)
            return True, options
        box = row.locator("input[type=radio][name], input[type=checkbox][name]").nth(options.index(answer))  # the option whose text is exactly the answer
        box.check()
        return box.is_checked(), options

    def _upload(self, page, job, c, row) -> bool:
        wanted = (c["name"] + " " + c["label"]).lower()
        if "cover" in wanted:
            path = forms.existing_file(getattr(job, "cover_pdf", None))
        elif c["name"] == "resume" or "resume" in wanted or " cv" in " " + wanted:
            path = forms.existing_file(getattr(job, "cv_pdf", None))
        else:
            path = None  # a file we were not given: not ours to invent
        if path is None:
            return False
        row.locator("input[type=file]").set_input_files(path)
        if c["name"] == "resume":  # Lever reads the resume and rewrites fields; wait for it, then fill over the top
            try:
                page.locator(".resume-upload-success, .resume-upload-failure").first.wait_for(state="visible", timeout=self.RESUME_PARSE_WAIT_MS)
            except PlaywrightTimeout:
                pass
            if page.locator(".resume-upload-oversize").is_visible():
                return False
        return True

    def _locate(self, page, resolve, c, row) -> bool:
        answer = forms.ask(resolve, c["label"], "text")
        if answer is None:
            return False
        box = row.locator("input[name=location]")
        box.fill("")
        box.press_sequentially(answer, delay=15)
        rows = page.locator(".dropdown-container .dropdown-location")
        try:
            rows.first.wait_for(state="visible", timeout=self.TYPEAHEAD_WAIT_MS)
        except PlaywrightTimeout:
            pass
        texts = [t.strip() for t in rows.all_inner_texts()]
        match = [i for i, t in enumerate(texts) if forms.norm(t) == forms.norm(answer)]  # an exact match or nothing: the first row may be another country
        if not match:
            box.fill("")
            return False
        rows.nth(match[0]).click()
        return bool(row.locator("input[name=selectedLocation]").input_value())

    # -- submit ------------------------------------------------------------------------------------------------------------------

    def submit(self, page) -> Receipt:
        forms.refuse_if_human(page)
        button = page.locator("form#application-form #btn-submit, form#application-form button[type=submit]:visible")
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
        if not state["form"] and state["ok"] and "application submitted" in state["ok"].lower():  # Lever's own confirmation element, form gone
            return Receipt(True, state["ok"])
        if state["form"]:
            return Receipt(False, "NOT CONFIRMED: the application form is still showing")
        return Receipt(False, "NOT CONFIRMED: the form is gone but the page has no Lever confirmation")
