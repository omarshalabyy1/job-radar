"""Hireeing (sources/remote.py), read from a saved real page: a search page that lists no job at all, for every role, is a
warning (changed), not a quiet day - a quiet day still lists older jobs, all stored before."""
import re
from pathlib import Path

from job_radar.sources import remote

FIXTURES = Path(__file__).parent / "fixtures"


class Reply:
    def __init__(self, text):
        self.text = text


def test_hireeing_warns_when_no_search_lists_a_job(monkeypatch, capsys):
    empty = (FIXTURES / "hireeing_no_roles.html").read_text(encoding="utf-8")  # hireeing.com/jobs?q=AI+Data+Engineer, 2026-11-14
    monkeypatch.setattr(remote, "get", lambda url, **params: Reply(empty))
    monkeypatch.setattr(remote.time, "sleep", lambda seconds: None)
    assert remote.hireeing(set()) == []
    assert "WARNING hireeing:" in capsys.readouterr().out


def test_hireeing_says_nothing_when_its_searches_list_only_stored_jobs(monkeypatch, capsys):
    listing = (FIXTURES / "hireeing_list.html").read_text(encoding="utf-8")
    stored = {"https://hireeing.com" + href for href in re.findall(r'href="(/jobs/[^"]+)"', listing)}
    monkeypatch.setattr(remote, "get", lambda url, **params: Reply(listing))
    monkeypatch.setattr(remote.time, "sleep", lambda seconds: None)
    assert remote.hireeing(stored) == []
    assert "WARNING" not in capsys.readouterr().out
