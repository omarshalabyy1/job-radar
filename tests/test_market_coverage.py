"""extract_market reads every due board inside its 600 s: each platform (one host, one request a second) is read by its own
thread, and Lever is asked as Chrome from the first request. On 2026-11-14 a run read 228 of 618 active boards, never
reached the 286 candidates, and was killed by its watchdog: api.lever.co stalled plain requests for 19 to 63 seconds a
board (curl_cffi: 2 to 4), and every platform waited in one queue."""

import threading
import time

from job_radar import sources, steps
from job_radar.sources import base


def test_lever_is_asked_as_chrome_from_the_first_request():
    assert {"api.lever.co", "api.eu.lever.co"} <= base.CHROME_ONLY


def test_each_platform_is_read_by_its_own_thread(monkeypatch):
    """Two platforms of 3 boards, each read taking 0.2 s: side by side about 0.6 s, one queue 1.2 s."""
    seen = {}

    def reader(ats):
        def read(token, company):
            seen.setdefault(ats, set()).add(threading.get_ident())
            time.sleep(0.2)
            if token == "broken":
                raise RuntimeError("boom")
            return [{"title": token}]
        return read

    monkeypatch.setattr(sources.market, "READERS", {"ashby": reader("ashby"), "lever": reader("lever")})
    boards = [("ashby", "a1", "A1"), ("lever", "l1", "L1"), ("ashby", "a2", "A2"), ("lever", "broken", "B"),
              ("ashby", "a3", "A3"), ("lever", "l3", "L3")]
    started = time.monotonic()
    results = list(steps.read_boards(boards))
    assert time.monotonic() - started < 1.0
    assert len(seen["ashby"]) == 1 and len(seen["lever"]) == 1 and seen["ashby"] != seen["lever"]
    assert sorted((ats, token) for ats, token, _ in results) == sorted((ats, token) for ats, token, _ in boards)
    assert [type(r).__name__ for ats, token, r in results if token == "broken"] == ["RuntimeError"]
    # a platform's boards come back in the order given: active and oldest first, then the candidates
    assert [token for ats, token, _ in results if ats == "ashby"] == ["a1", "a2", "a3"]


def test_no_board_is_started_once_the_budget_is_spent(monkeypatch):
    monkeypatch.setattr(sources.market, "READERS", {"ashby": lambda token, company: []})
    monkeypatch.setattr(sources, "time_left", lambda budget: 5)
    assert list(steps.read_boards([("ashby", "a1", "A1")])) == []


def test_every_due_board_is_selected_not_only_the_first_500():
    assert steps.MARKET_BOARDS >= 2000
