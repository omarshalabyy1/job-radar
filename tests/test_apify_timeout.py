"""An Apify run is given 600 s, and one that did not end SUCCEEDED says so in a WARNING. On 2026-11-14 the linkedin board's
run was cut by Apify's 300 s default with 599 of its 749 items, and the step reported it like any finished run."""

from job_radar.sources import apify


class Reply:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def fake_api(monkeypatch, status: str, items: list, calls: list):
    def post(url, **kwargs):
        calls.append(kwargs["params"])
        return Reply({"data": {"id": "r1", "status": status, "defaultDatasetId": "d1", "usageTotalUsd": 0.2}})

    monkeypatch.setattr(apify.requests, "post", post)
    monkeypatch.setattr(apify.requests, "get", lambda url, **kwargs: Reply(items))


def test_a_run_gets_600_seconds_and_we_wait_that_long(monkeypatch, capsys):
    calls = []
    fake_api(monkeypatch, "SUCCEEDED", [{"a": 1}], calls)
    apify.run("tok", "valig/linkedin-jobs-scraper", {}, 749, 0.3)
    assert calls[0]["timeout"] == 600 and apify.WAIT_SECONDS >= 600
    assert "WARNING" not in capsys.readouterr().out


def test_a_run_that_timed_out_is_a_warning_and_its_items_are_kept(monkeypatch, capsys):
    fake_api(monkeypatch, "TIMED-OUT", [{"a": 1}, {"a": 2}], [])
    out = apify.run("tok", "valig/linkedin-jobs-scraper", {}, 749, 0.3)
    assert len(out) == 2
    assert "WARNING apify valig/linkedin-jobs-scraper: run timed-out with 2 of up to 749 items" in capsys.readouterr().out
