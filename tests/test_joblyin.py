"""Joblyin's reader against two saved pages of its job list (tests/fixtures/joblyin_page*.html, read 2026-11-14), without
the network: page 1 has an Arabic Forasna card and LinkedIn reposts dated by a <time datetime>; page 2 is the list's last
page (no link to a page 3) with older cards dated "Nov 2, 2026"."""

import time
from pathlib import Path

import pytest

from job_radar import steps
from job_radar.sources import egypt

FIXTURES = Path(__file__).parent / "fixtures"


class Saved:
    """A saved page as requests gives it: Joblyin's Content-Type names no charset, so requests decodes its UTF-8 as
    Latin-1 in .text (checked live 2026-11-14); .content holds the bytes."""
    def __init__(self, name: str):
        self.content = (FIXTURES / name).read_bytes()
        self.text = self.content.decode("latin-1")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    monkeypatch.setattr(egypt, "JOBLYIN_HOURS", 100_000 * 24)  # the saved cards stay in the window whatever today is
    monkeypatch.setattr(egypt, "kept", lambda title: "Analyst" in title or "ML Engineer" in title or "دعم فني" in title)


def pages(asked: list):
    def get(url, **params):
        asked.append((url, params.get("page")))
        return Saved(f"joblyin_page{params['page']}.html")
    return get


def test_joblyin_reads_each_page_of_its_list_until_the_last(monkeypatch):
    asked = []
    monkeypatch.setattr(egypt, "get", pages(asked))
    rows = egypt.joblyin()
    assert asked == [("https://joblyin.com/jobs", 1), ("https://joblyin.com/jobs", 2)]  # page 2 links to no page 3
    by_url = {r["job_url"]: r for r in rows}
    assert list(by_url) == ["https://joblyin.com/jobs/forasna-447397", "https://joblyin.com/jobs/linkedin-4490309299",
                            "https://joblyin.com/jobs/linkedin-4469862778", "https://joblyin.com/jobs/data-analyst-001"]
    ml = by_url["https://joblyin.com/jobs/linkedin-4490309299"]
    assert (ml["source"], ml["title"], ml["company"], ml["location"], ml["date_posted"], ml["description"]) == (
        "joblyin", "Senior ML Engineer (Telecom/Banking)", "SSC HR Solutions", "Cairo, Cairo, Egypt", "2026-11-12", None)
    assert ml["payload"]["board"] == "linkedin" and not ml["payload"]["remote"]
    old = by_url["https://joblyin.com/jobs/linkedin-4469862778"]
    assert (old["title"], old["date_posted"]) == ("Junior Financial Analyst", "2026-11-02")  # the "Nov 2, 2026" format
    arabic = by_url["https://joblyin.com/jobs/forasna-447397"]
    assert arabic["location"] == "مدينة نصر, القاهرة" and arabic["payload"]["board"] == "forasna"
    assert by_url["https://joblyin.com/jobs/data-analyst-001"]["payload"]["remote"]


def test_joblyin_stops_at_the_first_page_with_nothing_in_its_window(monkeypatch):
    asked = []
    monkeypatch.setattr(egypt, "get", pages(asked))
    monkeypatch.setattr(egypt, "JOBLYIN_HOURS", 0)  # every saved card is older than today
    assert egypt.joblyin() == [] and len(asked) == 1


def test_joblyin_warns_when_its_list_shows_no_job_cards(monkeypatch, capsys):
    monkeypatch.setattr(egypt, "get", lambda url, **params: type("Page", (), {"content": b"<html><body></body></html>"})())
    assert egypt.joblyin() == []
    assert "WARNING joblyin: no job cards" in capsys.readouterr().out


def test_extract_egypt_reads_joblyin_after_wuzzuf_and_tanqeeb(monkeypatch):
    called = []
    monkeypatch.setattr(steps, "extract", lambda conn, *functions: called.extend(f.__name__ for f in functions))
    steps.extract_egypt(None)
    assert called == ["wuzzuf", "tanqeeb", "joblyin"]
