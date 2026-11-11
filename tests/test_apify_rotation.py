"""An account that refuses a run (HTTP 402: its 5 concurrent runs are taken, or its usage limit) hands the run to the next."""
import requests

from job_radar.sources import apify, social
from test_social import NOW, fake_run, items, settings


def refused(status: int):
    response = requests.Response()
    response.status_code = status
    response._content = b'{"error": {"type": "concurrent-runs-limit-exceeded"}}'
    return requests.HTTPError(response=response)


def test_a_refused_account_hands_the_run_to_the_next(monkeypatch, capsys):
    holds = settings(monkeypatch, "x")
    tried = []

    def run(token, *args):
        tried.append(token)
        if token == "a":
            raise refused(402)
        return fake_run(items("social_x.json"), 0.01)(token, *args)

    monkeypatch.setattr(apify, "run", run)
    monkeypatch.setattr(social, "utcnow", lambda: NOW)
    found = apify.apify_jobs()
    assert tried == ["a", "b"] and found and holds == {"apify-x": 72000}
    out = capsys.readouterr().out
    assert "account 1 refused (HTTP 402), trying the next" in out and "WARNING" not in out


def test_every_account_refusing_is_a_warning_and_no_hold(monkeypatch, capsys):
    holds = settings(monkeypatch, "x")
    monkeypatch.setattr(apify, "run", lambda token, *args: (_ for _ in ()).throw(refused(402)))
    assert apify.apify_jobs() == [] and holds == {}  # not held: the next run tries again
    assert "WARNING apify x: run failed" in capsys.readouterr().out
