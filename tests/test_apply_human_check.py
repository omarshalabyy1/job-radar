"""job_radar/apply/adapters/human_check.py: the page shows something only Omar passes (CAPTCHA, login wall, "verify you are human")."""
import pytest

from apply_form_helpers import browser, page  # noqa: F401  (fixtures)
from job_radar.apply.adapters import human_check

WIDGETS = {
    "recaptcha v2 box": '<form><div class="g-recaptcha" data-sitekey="k"></div></form>',
    "recaptcha invisible badge": '<form></form><div class="grecaptcha-badge"><iframe src="https://www.recaptcha.net/recaptcha/enterprise/anchor?k=x"></iframe></div>',
    "recaptcha loader script": '<form></form><script src="https://www.google.com/recaptcha/api.js"></script>',
    "hcaptcha box": '<form><div id="h-captcha" class="h-captcha" data-sitekey="k"></div></form>',
    "hcaptcha iframe": '<form></form><iframe src="https://newassets.hcaptcha.com/captcha/v1/abc/static/hcaptcha.html"></iframe>',
    "turnstile": '<form><div class="cf-turnstile" data-sitekey="k"></div></form>',
    "turnstile iframe": '<iframe src="https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/turnstile/if/ov2/x"></iframe>',
}
WALLS = {
    "login wall": '<h1>Sign in</h1><form><input type="email"><input type="password"></form>',
    "verify you are human": "<h1>Verify you are human</h1><p>Please wait while we check your browser.</p>",
    "just a moment": "<title>Just a moment...</title><h1>Checking your browser before accessing the site</h1>",
    "robot check": "<p>Are you a robot? Press and hold the button.</p>",
    "greenhouse emailed code": ('<form><label for="security_code">Security code</label><input id="security_code">'
                                "<p>A verification code was sent to a@b.c. To submit your application, enter the 8-character code to confirm you're a human.</p></form>"),
}
CLEAN = ('<form id="application-form"><label for="first_name">First name</label><input id="first_name">'
         '<label for="email">Email</label><input id="email" type="email"><button type="submit">Submit application</button></form>')


@pytest.mark.parametrize("html", WIDGETS.values(), ids=WIDGETS.keys())
def test_a_captcha_widget_needs_a_human(page, html):
    page.set_content(html)
    assert human_check.needs_human(page) is True


@pytest.mark.parametrize("html", WALLS.values(), ids=WALLS.keys())
def test_a_login_wall_or_human_check_page_needs_a_human(page, html):
    page.set_content(html)
    assert human_check.needs_human(page) is True


def test_a_plain_form_needs_no_human(page):
    page.set_content(CLEAN)
    assert human_check.needs_human(page) is False


def test_a_hidden_password_field_is_not_a_login_wall(page):
    page.set_content(CLEAN.replace("<button", '<input type="password" style="display:none"><button'))
    assert human_check.needs_human(page) is False
