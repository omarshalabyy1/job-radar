"""Shared by the apply-adapter tests: a real Chromium page that never touches the network, the saved real forms from
tests/fixtures/apply/, and small emulations of the two widgets a static fixture cannot run (Greenhouse's react-select dropdowns
and Lever's location typeahead). The emulations copy the DOM the live pages produced when probed on 2026-11-08 (read-only);
what the live pages do after a *successful* upload or submit was not probed (nothing was sent to any employer), so those parts are
assumptions and say so where they are used."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "apply"

# react-select as probed live: a click on the combobox opens #react-select-<id>-listbox with div[role=option]; picking one closes
# it and shows .select__single-value. Dropdowns whose options load while typing (location, school) get them from window.__typeahead.
REACT_SELECT_SHIM = """
(() => {
  const state = {};
  const render = (input, options) => {
    close(input);
    const box = input.closest('.select__container') || input.parentElement;
    const menu = document.createElement('div'); menu.className = 'select__menu';
    const list = document.createElement('div'); list.className = 'select__menu-list'; list.setAttribute('role', 'listbox');
    list.id = 'react-select-' + input.id + '-listbox';
    options.forEach((text, i) => {
      const o = document.createElement('div'); o.className = 'select__option'; o.setAttribute('role', 'option');
      o.id = 'react-select-' + input.id + '-option-' + i; o.textContent = text;
      o.addEventListener('click', () => {
        const vc = box.querySelector('.select__value-container');
        vc.querySelectorAll('.select__single-value, .select__placeholder').forEach(e => e.remove());
        const v = document.createElement('div'); v.className = 'select__single-value'; v.textContent = text; vc.prepend(v);
        input.value = ''; close(input);
      });
      list.appendChild(o);
    });
    menu.appendChild(list); box.appendChild(menu); input.setAttribute('aria-expanded', 'true');
  };
  const close = input => { const m = document.getElementById('react-select-' + input.id + '-listbox'); if (m) m.parentElement.remove();
                           input.setAttribute('aria-expanded', 'false'); };
  document.querySelectorAll('input.select__input[role=combobox]').forEach(input => {
    const fixed = JSON.parse(input.getAttribute('data-fixture-options') || '[]');
    input.addEventListener('click', () => { if (fixed.length) render(input, fixed); });
    input.addEventListener('keydown', e => { if (e.key === 'Escape') close(input); });
    input.addEventListener('input', () => {
      const t = (window.__typeahead || {})[input.id];
      if (t && input.value) render(input, t); else if (!fixed.length) close(input);
    });
  });
  // file upload as probed live: choosing a file starts an upload at once and a progress bar shows. The finished state is an ASSUMPTION
  // (bar gone, file name shown); window.__upload_stuck keeps the bar up to model an upload that never finishes.
  document.querySelectorAll('.file-upload input[type=file]').forEach(input => {
    input.addEventListener('change', () => {
      const wrap = input.closest('.file-upload').querySelector('.file-upload__wrapper');
      const bar = document.createElement('div'); bar.className = 'file-upload__progressbar'; bar.setAttribute('role', 'progressbar');
      wrap.appendChild(bar);
      if (!window.__upload_stuck) setTimeout(() => { bar.remove(); const n = document.createElement('div');
        n.className = 'file-upload__filename'; n.textContent = input.files[0].name; wrap.appendChild(n); }, 30);
    });
  });
  // submit: count the click; window.__after (HTML) replaces the page the way a submitted form would.
  window.__submits = 0;
  document.querySelectorAll('form button[type=submit]').forEach(btn => btn.addEventListener('click', e => {
    e.preventDefault(); window.__submits += 1; if (window.__after) document.body.innerHTML = window.__after;
  }));
})();
"""

# Lever: typing in the location box lists .dropdown-location rows (probed live: "Cairo, EGY", "New Cairo, Cairo, EGY", "Cairo, GA, USA");
# picking one fills the hidden selectedLocation. The resume parser (window.__parse = a name) autofills Full name after an upload, as
# Lever's does, which is why the adapter must fill AFTER uploading.
LEVER_SHIM = """
(() => {
  const input = document.querySelector('input[name=location]');
  if (input) input.addEventListener('input', () => {
    document.querySelectorAll('.dropdown-container').forEach(e => e.remove());
    const rows = (window.__locations || []).filter(Boolean); if (!rows.length || !input.value) return;
    const box = document.createElement('div'); box.className = 'dropdown-container';
    const res = document.createElement('div'); res.className = 'dropdown-results';
    rows.forEach((text, i) => { const r = document.createElement('div'); r.className = 'dropdown-location'; r.id = 'location-' + i;
      r.textContent = text; r.addEventListener('click', () => { input.value = text;
        document.querySelector('input[name=selectedLocation]').value = JSON.stringify({name: text}); box.remove(); }); res.appendChild(r); });
    box.appendChild(res); input.parentElement.appendChild(box);
  });
  document.querySelectorAll('input[type=file]').forEach(f => f.addEventListener('change', () => {
    if (window.__parse) setTimeout(() => {
      const n = document.querySelector('input[name=name]'); if (n) n.value = window.__parse;
      const ok = document.querySelector('.resume-upload-success'); if (ok) ok.style.display = 'block';
    }, 30);
  }));
  window.__submits = 0;
  const b = document.getElementById('btn-submit');
  if (b) b.addEventListener('click', () => { window.__submits += 1; if (window.__after) document.body.innerHTML = window.__after; });
})();
"""


@pytest.fixture(scope="module")
def browser():
    """Chromium for one apply-adapter test module, closed before the next module runs. It must not outlive its module: Playwright's sync API
    keeps an event loop running in the thread while it is started, and any other test that starts its own (test_tailor's PDF check) would fail
    with "use the Async API instead". Import `browser` and `page` from here in other adapters' tests."""
    from playwright.sync_api import Error, sync_playwright
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Error as e:
            pytest.skip(f"no Chromium (python -m playwright install chromium): {e!r}"[:160])
        yield b
        b.close()


@pytest.fixture
def page(browser):
    ctx = browser.new_context()
    ctx.route("**/*", lambda route: route.abort())  # tests never touch the network
    pg = ctx.new_page()
    yield pg
    ctx.close()


def load(page, name: str, shim: str = "", transform=None, **window_vars) -> None:
    """Open a saved form (tests/fixtures/apply/<name>.html). `transform(html)` edits the markup first; window_vars become
    window.__<name> values read by the shims."""
    html = (FIXTURES / f"{name}.html").read_text(encoding="utf-8")
    page.set_content(transform(html) if transform else html)
    if window_vars:
        page.evaluate("v => Object.assign(window, v)", {f"__{k}": v for k, v in window_vars.items()})
    if shim:
        page.evaluate(shim)


def pdfs(tmp_path: Path):
    cv, cover = tmp_path / "cv.pdf", tmp_path / "cover.pdf"
    for f in (cv, cover):
        f.write_bytes(b"%PDF-1.4\n%%EOF\n")
    return cv, cover


def make_job(tmp_path: Path, board: str = "greenhouse", with_cover: bool = True, with_cv: bool = True):
    cv, cover = pdfs(tmp_path)
    return SimpleNamespace(job_id=1, company="Example", board=board, url=f"https://example.invalid/{board}", score=8,
                           cv_pdf=str(cv) if with_cv else None, cover_pdf=str(cover) if with_cover else None)


class Asked:
    """A `resolve` stand-in that answers only from a fixed dict (matched like the real one: normalised phrase inside the question)
    and records every call, so tests can see what was asked and prove no answer came from anywhere else."""

    def __init__(self, approved: dict[str, str] | None = None):
        self.approved = approved or {}
        self.calls: list[tuple[str, str, tuple | None]] = []

    def __call__(self, question: str, field_type: str = "text", options=None):
        self.calls.append((question, field_type, tuple(options) if options else None))
        q = " ".join("".join(c if c.isalnum() else " " for c in question.lower()).split())
        for phrase, answer in self.approved.items():
            if phrase in q:
                if options:
                    norm = lambda s: " ".join("".join(c if c.isalnum() else " " for c in s.lower()).split())  # noqa: E731
                    return next((o for o in options if norm(o) == norm(answer)), None)
                return answer
        return None

    def questions(self) -> list[str]:
        return [c[0] for c in self.calls]


def values(page, selector: str = "form input, form textarea") -> dict[str, str]:
    """Every text-ish value now in the form (id or name -> value), to assert what was typed and that nothing else was."""
    return page.evaluate("""sel => { const o = {}; document.querySelectorAll(sel).forEach(e => {
        if (['file', 'hidden', 'checkbox', 'radio', 'submit'].includes(e.type) || !e.value) return; o[e.id || e.name] = e.value; }); return o; }""",
                         selector)


CONFIRMATIONS = {
    # Greenhouse's own words, from its public locale file confirmation.en (title and body), not a captured page: no real application was sent.
    "greenhouse": "<main><h1>We've received your application.</h1><p>Thank you for applying to Example. We'll reach out to you if we're "
                  "interested in moving forward.</p></main>",
    # Lever's /thanks page, captured 2026-11-08 (GET, nothing submitted).
    "lever": '<div class="confirmation-message"><h3 data-qa="msg-submit-success">Application submitted!</h3></div>',
}

__all__ = ["REACT_SELECT_SHIM", "LEVER_SHIM", "browser", "page", "load", "make_job", "Asked", "values", "CONFIRMATIONS", "json"]


def strip_captcha(page) -> None:
    """The saved real forms all carry a CAPTCHA widget (Greenhouse: invisible reCAPTCHA Enterprise badge; Lever: hCaptcha). Fill and submit
    are never called on such a page, so their tests run on the same form with the widget removed."""
    page.evaluate("""() => document.querySelectorAll(
        '.grecaptcha-badge, iframe[src*=recaptcha], script[src*=recaptcha], .h-captcha, script[src*=hcaptcha], input[name=h-captcha-response]'
    ).forEach(e => e.remove())""")


def serve(page, pages: dict[str, str]) -> list[str]:
    """Answer these exact URLs with HTML (everything else stays blocked), and return the list of URLs the page asked for."""
    asked: list[str] = []

    def handler(route):
        url = route.request.url
        asked.append(url)
        if url in pages:
            route.fulfill(status=200, content_type="text/html", body=pages[url])
        else:
            route.abort()
    page.route("**/*", handler)
    return asked
