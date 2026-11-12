"""A job asking 5 or more years (settings.yaml experience.hide_min_years) is hidden like a restricted one: your CV shows
about 2, so a 7-year senior role is not one you can apply for. A status of yours shows it again; no years is no reason."""
import pytest

from job_radar import config

SWITCHES = {"eligibility": False, "auto_hide": False, "grade": False}


@pytest.mark.parametrize("status, years, expected", [
    ("new", 7, "asks 7+ years"), ("new", 5, "asks 5+ years"), ("new", 4, None), ("new", None, None),
    ("new", float("nan"), None), ("saved", 9, None), ("applied", 9, None)])
def test_a_new_job_asking_many_years_is_hidden(status, years, expected):
    assert config.hidden_reason(status, None, None, SWITCHES, years) == expected


def test_the_cap_is_a_setting():
    assert config.SETTINGS["experience"]["hide_min_years"] == 5
