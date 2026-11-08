"""Is there something on the page only Omar passes? A CAPTCHA widget (reCAPTCHA, hCaptcha, Turnstile, visible or invisible), a login wall, or
a "verify you are human" page (a bot check, or Greenhouse's emailed security code). Then the runner parks the job and sends ntfy; the adapter's
`fill` and `submit` are never called, and refuse if they are. Everything on the page is data: a posting that contains these words can only
make a job park, which is the safe direction; no text on a page can make this return False."""

from __future__ import annotations

import re

CAPTCHA = ", ".join([
    ".g-recaptcha", ".grecaptcha-badge", ".h-captcha", ".cf-turnstile", "[data-sitekey]",
    "iframe[src*='recaptcha']", "iframe[src*='hcaptcha']", "iframe[src*='challenges.cloudflare.com']", "iframe[src*='turnstile']",
    "script[src*='recaptcha']", "script[src*='hcaptcha.com']", "script[src*='challenges.cloudflare.com/turnstile']",
    "textarea[name='g-recaptcha-response']", "input[name='h-captcha-response']", "input[name='cf-turnstile-response']",
])
CODE_FIELD = "input#security_code, input[name='security_code']"  # Greenhouse: "enter the 8-character code to confirm you're a human"
HUMAN_WORDS = re.compile(
    r"verify (that )?you(?:'|’)?re (a )?human|verify you are (a )?human|confirm you(?:'|’)?re (a )?human|confirm you are (a )?human|"
    r"are you a (robot|human)|checking your browser|just a moment|press (and|&) hold|unusual traffic from your|"
    r"(sign|log) ?in to (continue|apply|view)|please (sign|log) ?in", re.I)


def needs_human(page) -> bool:
    if page.locator(CAPTCHA).count() or page.locator("input[type=password]:visible").count() or page.locator(CODE_FIELD).count():
        return True
    text = page.evaluate("() => (document.title || '') + '\\n' + (document.body ? document.body.innerText : '')")
    return bool(HUMAN_WORDS.search(text))
