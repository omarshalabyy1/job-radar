"""Shared helpers for the form-adapter tests (tests/test_apply_adapter_<board>.py): one Chromium per test file, a page with every
network request aborted (a test never touches a board), the saved forms in tests/fixtures/apply/, a stand-in for answers.resolve and a
tailored job with real PDF files. The pages are saved DOM without scripts, so a test that needs the board's own JavaScript (a success
message after a click) adds a few lines of it itself."""

from __future__ import annotations

import pytest
from pathlib import Path

from job_radar.apply.runner import Prepared

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "apply"


def fixture_html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")  # one per test file: Playwright cannot start twice at once
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser):
    ctx = browser.new_context()
    ctx.route("**/*", lambda route: route.abort())  # no network: a saved form is all a test may load
    p = ctx.new_page()
    yield p
    ctx.close()


@pytest.fixture
def job(tmp_path):
    cv, cover = tmp_path / "Omar Shalaby Acme CV.pdf", tmp_path / "Omar Shalaby Acme Cover Letter.pdf"
    cv.write_bytes(b"%PDF-1.4 cv")
    cover.write_bytes(b"%PDF-1.4 cover")
    return Prepared(job_id=1, company="Acme", board="x", url="https://example.com/job", score=8, title="Data Engineer",
                    cv_pdf=cv, cover_pdf=cover)


def resolver(answers: dict[str, str]):
    """A stand-in for answers.resolve: the first key found in the question (lower case) gives the answer; a select gets an answer only
    when it is one of the options, like the real one. `.calls` lists what was asked."""
    calls: list[tuple] = []

    def resolve(question, field_type="text", options=None):
        calls.append((question, field_type, options))
        for key, value in answers.items():
            if key in question.lower():
                return value if not options or value in options else None
        return None

    resolve.calls = calls
    return resolve


COUNT_CLICKS = """(sel) => { window.__clicks = 0; document.querySelectorAll(sel).forEach(
    b => b.addEventListener('click', e => { window.__clicks++; e.preventDefault(); })); }"""


def count_clicks_on(page, selector: str) -> None:
    page.evaluate(COUNT_CLICKS, selector)


def clicks(page) -> int:
    return page.evaluate("window.__clicks")


def value_of(page, selector: str) -> str:
    return page.locator(selector).first.input_value()


def uploaded(page, selector: str) -> list[str]:
    return page.locator(selector).first.evaluate("e => Array.from(e.files).map(f => f.name)")


def labels(fields) -> list[str]:
    return [f.label for f in fields]


@pytest.fixture(autouse=True)
def fast_waits(monkeypatch):
    """The saved pages are complete at once: nothing to wait for."""
    from job_radar.apply.adapters import _ats

    monkeypatch.setattr(_ats, "FORM_WAIT_MS", 300)
    monkeypatch.setattr(_ats, "SETTLE_MS", 100)
