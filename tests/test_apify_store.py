"""A board's postings are stored before the board is held for 20 hours, so a step killed between two boards loses nothing."""
import pytest


from job_radar.sources import apify, social
from test_social import NOW, fake_run, items, settings


def test_a_board_is_stored_before_it_is_held_so_a_killed_step_loses_nothing(monkeypatch):
    holds = settings(monkeypatch, "x", "reddit")
    stored = []

    def run(token, actor, *args):
        if "twitter" in actor:
            return fake_run(items("social_x.json"), 0.01, [])(token, actor, *args)
        raise RuntimeError("the container was stopped")  # not a RequestException: the whole step dies

    monkeypatch.setattr(apify, "run", run)
    monkeypatch.setattr(social, "utcnow", lambda: NOW)
    with pytest.raises(RuntimeError):
        apify.apify_jobs(store=lambda name, found: stored.append((name, len(found), dict(holds))))
    assert stored == [("x", 5, {})]  # stored while the board was not yet held
    assert holds == {"apify-x": 72000}  # then held: the next run does not pay for it again
