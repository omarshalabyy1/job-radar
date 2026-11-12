"""Bayt does not read the boolean `"a" OR "b"` term Indeed takes: it answers 0 fresh jobs to it, every day, with no error.
So Bayt is searched with each role's first plain search phrase (settings.yaml roles: search), Indeed with the boolean one."""
import jobspy
import pandas as pd

from job_radar import config
from job_radar.sources import boards


def searched(monkeypatch, site):
    terms = []
    monkeypatch.setattr(jobspy, "scrape_jobs", lambda **kw: terms.append(kw["search_term"]) or pd.DataFrame())
    monkeypatch.setattr(boards.time, "sleep", lambda s: None)
    monkeypatch.setattr(boards, "waiting", lambda host: 0)
    monkeypatch.setattr(boards, "time_left", lambda budget: 999)
    boards.board(site, [("Egypt", "Egypt", "egypt")])
    return terms


def test_bayt_gets_each_roles_first_plain_phrase_and_indeed_the_boolean_term(monkeypatch):
    assert searched(monkeypatch, "bayt") == [role["search"][0] for role in config.SETTINGS["roles"]]
    assert searched(monkeypatch, "indeed") == [term for _, _, term, _ in config.ROLES]
