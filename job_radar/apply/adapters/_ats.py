"""What the ATS form adapters (ashby, recruitee, smartrecruiters, workable) share, so each one is only what is true of its board:
its hosts, where its form is, its submit button and its own confirmation.

The rules every adapter keeps live here, once:
- `human_check`: a CAPTCHA widget (visible or an invisible badge), a login wall or a verify-human / blocked page is `needs_human`;
  nothing is filled or clicked there (fill and submit look again before they act).
- `fill_form`: an answer comes only from `resolve(question, field_type, options)`; the question is the field's label and nothing else on the
  page (descriptions, banners and job text are data and are never read as instructions or passed on); None means "not approved": the
  field stays empty and, if required, is returned. A control this module cannot read (a custom dropdown, a yes/no button pair) is
  returned as required too, never guessed. CV and cover letter come from `job.cv_pdf` and `job.cover_pdf`. Nothing here clicks submit.
- `wait_for_receipt`: after the one click, the board's own confirmation must show; page text elsewhere is not a receipt."""

from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from .base import Field, Receipt

FORM_WAIT_MS = 10_000   # a single-page app draws its form after the page loads
SETTLE_MS = 3_000       # then lets a CAPTCHA script add its widget before anyone looks for it

CAPTCHA_SELECTOR = ", ".join([
    'iframe[src*="recaptcha"]', 'iframe[src*="hcaptcha"]', 'iframe[src*="captcha-assets"]', 'iframe[src*="turnstile"]',
    'iframe[src*="challenges.cloudflare.com"]', 'iframe[title*="captcha" i]', 'iframe[title*="security challenge" i]',
    ".g-recaptcha", ".h-captcha", ".cf-turnstile", ".grecaptcha-badge", "[data-sitekey]",
    'textarea[name="g-recaptcha-response"]', 'textarea[name="h-captcha-response"]', 'input[name="cf-turnstile-response"]',
    '[id*="captcha" i]', '[class*="captcha" i]'])

_HUMAN_CHECK = """(captcha) => {
  const text = (document.body ? document.body.innerText : '').toLowerCase().replace(/\\s+/g, ' ');
  const head = (document.title + ' ' + Array.from(document.querySelectorAll('h1')).map(h => h.innerText).join(' ')).toLowerCase();
  if (document.querySelector(captcha)) return 'a CAPTCHA widget is on the page';
  if (/verify (that )?you(\\'re| are) (a )?human|verifying you are human|confirm you are human|are you a robot|unusual traffic|checking your browser|complete the security check|press (&|and) hold/.test(text))
    return 'a verify-human page';
  if (/just a moment|attention required|access denied|error 4(03|29)|too many requests|request blocked/.test(head))
    return 'a blocked page';
  const password = Array.from(document.querySelectorAll('input[type=password]')).some(i => !i.closest('[hidden]'));
  if (password) return 'a login wall';
  if (/(log ?in|sign ?in) (to|before) (apply|continue)|create an account to apply|you must be (logged|signed) in/.test(text)
      && !document.querySelector('input[type=email], input[name*=email i]')) return 'a login wall';
  return '';
}"""

_COLLECT = """([scopeSel, skipSel]) => {
  const scope = document.querySelector(scopeSel) || document;
  const clean = s => (s || '').replace(/\\s+/g, ' ').replace(/\\s*\\*\\s*$/, '').trim();
  const textOf = el => clean(el ? el.textContent : '');
  const labelOf = el => {
    if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) return textOf(l); }
    const by = el.getAttribute('aria-labelledby');
    if (by) { const t = clean(by.split(/\\s+/).map(i => textOf(document.getElementById(i))).join(' ')); if (t) return t; }
    const wrap = el.closest('label'); if (wrap) return textOf(wrap);
    return clean(el.getAttribute('aria-label') || el.getAttribute('placeholder') || '');
  };
  const groupLabel = el => {
    const fs = el.closest('fieldset'); const legend = fs && fs.querySelector('legend');
    if (legend) return textOf(legend);
    const g = el.closest('[role=radiogroup]'); const by = g && g.getAttribute('aria-labelledby');
    return by ? clean(by.split(/\\s+/).map(i => textOf(document.getElementById(i))).join(' ')) : (g ? clean(g.getAttribute('aria-label')) : '');
  };
  const out = []; const groups = {}; let n = 0;
  const skipped = el => el.closest('[hidden]') || (skipSel && el.closest(skipSel)) || el.disabled || el.readOnly;
  for (const el of scope.querySelectorAll('input, textarea, select, [role=combobox]')) {
    const tag = el.tagName.toLowerCase(), type = (el.getAttribute('type') || 'text').toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset', 'search'].includes(type) && tag === 'input') continue;
    if (/captcha|turnstile/i.test(el.name || el.id || '') || skipped(el)) continue;
    el.setAttribute('data-jr', String(++n));
    const sel = '[data-jr="' + n + '"]';
    if (el.getAttribute('role') === 'combobox' && tag !== 'input' && tag !== 'select') {
      if (/calling code|country-select/i.test((el.getAttribute('aria-label') || '') + ' ' + el.id)) continue;  // the phone prefix box
      out.push({kind: 'unsupported', label: labelOf(el) || groupLabel(el), required: true, options: [], sel});
      continue;
    }
    if (type === 'radio') {
      const key = el.name || sel;
      const g = groups[key] || (groups[key] = {kind: 'radio', label: groupLabel(el) || labelOf(el), required: false, options: [], sel: ''});
      if (!g.options.length) out.push(g);
      g.required = g.required || el.required || el.getAttribute('aria-required') === 'true';
      const l = el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      g.options.push({label: l ? textOf(l) : el.value, sel});
      continue;
    }
    const required = el.required || el.getAttribute('aria-required') === 'true';
    if (tag === 'select') {
      const options = Array.from(el.options).filter(o => o.value !== '' && !o.disabled).map(o => ({label: clean(o.textContent), sel: o.value}));
      out.push({kind: 'select', label: labelOf(el), required, options, sel});
    } else if (type === 'file') out.push({kind: 'file', label: labelOf(el), required, options: [], sel});
    else if (type === 'checkbox') out.push({kind: 'checkbox', label: labelOf(el), required, options: [], sel});
    else out.push({kind: tag === 'textarea' ? 'text' : (type === 'number' ? 'number' : 'text'), label: labelOf(el), required, options: [], sel});
  }
  return out;
}"""

_BLOCKING = "this page has no application form to fill"
_YES = {"yes", "y", "true", "1", "checked", "agree", "i agree", "accept", "i accept"}


def host_of(url: str) -> str:
    return (urlsplit(url or "").hostname or "").lower()


def on_host(url: str, hosts: tuple[str, ...], suffixes: tuple[str, ...] = ()) -> bool:
    """True for exactly these hosts or any subdomain of these suffixes ("recruitee.com" matches "acme.recruitee.com", never
    "recruitee.com.evil.example")."""
    host = host_of(url)
    return host in hosts or any(host == s or host.endswith("." + s) for s in suffixes)


def human_check(page) -> str:
    """Why a person must look at this page ('' when nothing needs one). A page that cannot be read (closed, navigating) counts as needing
    one, so nothing is filled or clicked blind."""
    try:
        return page.evaluate(_HUMAN_CHECK, CAPTCHA_SELECTOR)
    except Exception as e:  # noqa: BLE001
        return f"the page cannot be read ({type(e).__name__})"


def open_form(page, form_selector: str, application_url=None) -> bool:
    """Make sure the application form is on the page and drawn: from a posting page go to the form's own address
    (`application_url(current url)`), then wait for the form and let the page's scripts settle. False when there is no form."""
    try:
        if not page.locator(form_selector).count() and application_url:
            target = application_url(page.url)
            if target and target != page.url:
                page.goto(target)
        page.wait_for_selector(form_selector, timeout=FORM_WAIT_MS)
    except Exception:  # noqa: BLE001  (a timeout is "no form"; a closed page too)
        return False
    try:
        page.wait_for_load_state("networkidle", timeout=SETTLE_MS)
    except Exception:  # noqa: BLE001
        pass
    return True


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split())


def cv_or_cover(label: str, job) -> Path | None:
    """The tailored PDF a file field asks for: the cover letter by its label, the CV for "resume" / "CV"; any other upload gets nothing."""
    text = _norm(label)
    path = getattr(job, "cover_pdf", None) if re.search(r"\bcover\b", text) else \
        getattr(job, "cv_pdf", None) if re.search(r"\b(resume|cv|curriculum)\b", text) else None
    return Path(path) if path and Path(path).is_file() and Path(path).suffix.lower() == ".pdf" else None


def _field(q: dict) -> Field:
    kind = {"radio": "select", "unsupported": "text"}.get(q["kind"], q["kind"])
    return Field(q["label"] or "(unlabelled field)", kind, tuple(o["label"] for o in q["options"]), q["required"])


def fill_form(page, job, resolve, scope: str, skip: str = "", entry: str = "") -> list[Field]:
    """Fill every control inside `scope` that has an approved answer (and the CV / cover letter); return the required ones that
    could not be filled. `entry` (optional) is the selector of one question's block: a block with no control this module can read is
    returned as a required field too."""
    if not page.locator(scope).count():
        return [Field(_BLOCKING, "text", (), True)]
    questions = page.evaluate(_COLLECT, [scope, skip])
    missing: list[Field] = []
    for q in questions:
        label, kind, loc = q["label"], q["kind"], page.locator(q["sel"]).first
        done = False
        if kind == "unsupported":
            pass
        elif kind == "file":
            if (pdf := cv_or_cover(label, job)) is not None:
                loc.set_input_files(str(pdf))
                done = True
        else:
            names = [o["label"] for o in q["options"]]
            answer = resolve(label, {"radio": "select"}.get(kind, kind), names or None)
            if answer is not None:
                done = _set(page, q, loc, answer, names)
        if not done and q["required"]:
            missing.append(_field(q))
    if entry:
        for i in range(page.locator(entry).count()):
            block = page.locator(entry).nth(i)
            if not block.locator("input:not([type=hidden]), textarea, select").count():
                title = " ".join(block.locator("label, legend, h3, h4").first.inner_text().split()) if block.locator("label, legend, h3, h4").count() else ""
                missing.append(Field(title or "(question with no readable control)", "text", (), True))
    return missing


def _tick(page, loc) -> bool:
    """Check a radio or checkbox the way a person does: the input, else its label (boards hide the input and style the label), else
    the input's own click event (something drawn over it); and only count it when the box really is checked afterwards."""
    ident = loc.get_attribute("id")
    tries = [lambda: loc.check(timeout=1500)]
    if ident:
        tries.append(lambda: page.locator(f'label[for="{ident}"]').first.click(timeout=1500))
    tries.append(lambda: loc.evaluate("e => e.click()"))
    for attempt in tries:
        try:
            attempt()
        except Exception:  # noqa: BLE001  (covered, detached or no label: try the next way)
            continue
        if loc.is_checked():
            return True
    return False


def _set(page, q: dict, loc, answer: str, names: list[str]) -> bool:
    kind = q["kind"]
    if kind == "checkbox":
        return _norm(answer) in _YES and _tick(page, loc)
    if kind == "radio":
        pick = next((o for o in q["options"] if _norm(o["label"]) == _norm(answer)), None)
        if pick is None:
            return False
        return _tick(page, page.locator(pick["sel"]).first)
    if kind == "select":
        pick = next((o for o in q["options"] if _norm(o["label"]) == _norm(answer)), None)
        if pick is None:
            return False
        loc.select_option(value=pick["sel"])
        return True
    loc.fill(answer)
    return True


def wait_for_receipt(page, read_confirmation, seconds: float) -> Receipt:
    """Poll the board's own confirmation after the click (`read_confirmation(page) -> text or None`) for up to `seconds`."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            text = read_confirmation(page)
        except Exception:  # noqa: BLE001  (the page is navigating: look again)
            text = None
        if text:
            return Receipt(True, text)
        if time.monotonic() >= deadline:
            return Receipt(False, "no confirmation from the board after submit")
        page.wait_for_timeout(250)
