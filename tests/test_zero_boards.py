"""The boards that gave 0 postings in 7 days (scripts/audit_apps.py, 2026-11-10): the readers that were at fault, each against
a saved sample of its site (tests/fixtures/), without the network."""

import json
import time
from pathlib import Path

import pytest

from job_radar.sources import boards

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda seconds: None)


class Saved:
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


def freehire_site(monkeypatch):
    """freehire's search API, from a real response of its Egypt filter (freehire_egypt.json); the remote search finds nothing.
    Returns the list of the parameters each search was asked with."""
    asked = []

    def get(url, **params):
        asked.append(params)
        return Saved(json.loads((FIXTURES / "freehire_egypt.json").read_text(encoding="utf-8")) if params.get("countries") == "eg"
                     else {"data": []})

    monkeypatch.setattr(boards, "get", get)
    monkeypatch.setattr(boards, "KEYWORDS", ["data engineer"])
    return asked


def test_freehire_asks_for_the_last_three_days_of_egypt_not_one(monkeypatch):
    """Egypt's jobs there are dated to the day (midnight), so at noon yesterday's job is already 36 hours old: a one day
    window never saw it. 17 Egypt data and AI jobs were listed for the week, none was stored."""
    asked = freehire_site(monkeypatch)
    boards.freehire()
    egypt = next(p for p in asked if p.get("countries") == "eg")
    assert egypt["posted_within_days"] >= 3


def test_freehire_keeps_an_egypt_job_whose_location_only_says_eg(monkeypatch):
    """"Almazah, EG" and "Cairo, Cairo, EGY" are Egypt (the board's countries say so); place_of knew neither word."""
    freehire_site(monkeypatch)
    rows = boards.freehire()
    kept = {r["title"]: r["location"] for r in rows}
    assert "Senior Data Engineer" in kept and "Egypt" in kept["Senior Data Engineer"]
    assert "Sr. Data Engineer - Spark" in kept
    assert "Data Entry - Hurghada Q4 2026 Replacement" not in kept  # not one of your roles


def jooble_site(monkeypatch):
    """Jooble's API, from a real response of its "Remote" location (jooble_remote.json); Egypt has no data job there.
    Returns the (location, payload) of every search."""
    asked = []

    def fetch(method, url, json=None, **kw):
        asked.append(json)
        return Saved(globals()["json"].loads((FIXTURES / "jooble_remote.json").read_text(encoding="utf-8")) if json["location"] == "Remote"
                     else {"jobs": []})

    monkeypatch.setenv("JOOBLE_API_KEY", "test-key")
    monkeypatch.setattr(boards, "fetch", fetch)
    monkeypatch.setattr(boards, "since", lambda: "2000-01-01")
    monkeypatch.setattr(boards, "KEYWORDS", ["data engineer"])
    monkeypatch.setattr(boards, "PLACES", [("Egypt", "Egypt", "egypt")])
    return asked


def test_jooble_also_searches_remote_jobs_of_the_last_day(monkeypatch):
    """Jooble has no remote filter and PLACES has no Remote, so it was only asked for places: its Egypt searches hold no data
    job, and its USA, Canada and Europe ones are barred. Its location "Remote" holds about 130 of your roles a day."""
    asked = jooble_site(monkeypatch)
    boards.jooble()
    remote = [p for p in asked if p["location"] == "Remote"]
    assert len(remote) == 1 and remote[0]["keywords"] == "data engineer" and remote[0]["datecreatedfrom"] == "2000-01-01"
    assert all("datecreatedfrom" not in p for p in asked if p["location"] != "Remote")


def test_jooble_remote_jobs_are_kept_unless_their_place_ties_them_elsewhere(monkeypatch):
    from job_radar.eligibility import not_for_egypt
    jooble_site(monkeypatch)
    rows = {r["company"]: r for r in boards.jooble()}
    assert set(rows) == {"Egen", "Brex", "SpaceX"} and all(r["searched_for"] == "Remote" for r in rows.values())
    barred = {c: not_for_egypt(r["source"], r["location"], r["searched_for"], r["payload"]) for c, r in rows.items()}
    assert barred["Egen"] is None and barred["Brex"] == "open to USA only"  # SpaceX's "Texas" is left to the eligibility step
