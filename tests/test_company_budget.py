"""extract_companies reads 238 career sites: with the 150 s every extract gets it left 35-49 sites for the next run and 30 were never
read (audit 2026-11-10). It has its own time budget, and the Airflow task a timeout longer than that budget."""
from pathlib import Path

from job_radar import config

ROOT = Path(__file__).resolve().parent.parent


def test_the_company_step_has_its_own_longer_budget():
    assert config.COMPANY_SECONDS == 600 > config.EXTRACT_SECONDS


def test_the_company_readers_use_that_budget_and_the_dag_waits_for_it():
    assert "EXTRACT_SECONDS" not in (ROOT / "job_radar" / "sources" / "companies.py").read_text(encoding="utf-8")
    steps_text = (ROOT / "job_radar" / "steps.py").read_text(encoding="utf-8")
    assert "if sources.time_left(COMPANY_SECONDS) < 70:" in steps_text
    dag = (ROOT / "dags" / "job_radar.py").read_text(encoding="utf-8")
    assert 'step("extract_companies", retries=2, retry_delay=timedelta(minutes=2), execution_timeout=timedelta(seconds=720))' in dag


def test_the_market_harvest_has_its_own_budget_so_the_candidate_boards_get_read():
    """157 active boards at a second each used the 150 s before any of the 377 candidate boards was reached (audit 2026-11-10)."""
    assert config.MARKET_SECONDS == 600
    assert "if sources.time_left(MARKET_SECONDS) < 20:" in (ROOT / "job_radar" / "steps.py").read_text(encoding="utf-8")
    dag = (ROOT / "dags" / "job_radar.py").read_text(encoding="utf-8")
    assert 'step("extract_market", retries=2, retry_delay=timedelta(minutes=2), execution_timeout=timedelta(seconds=720))' in dag


def test_the_long_extract_steps_have_a_watchdog_past_their_budget_and_inside_their_timeout():
    """The 210 s watchdog stopped the first 600 s company run at 210 s: those two steps arm a longer one (2026-11-10)."""
    from job_radar import __main__ as main

    assert config.COMPANY_SECONDS < main.LONG_STEPS["extract_companies"] < 720
    assert config.MARKET_SECONDS < main.LONG_STEPS["extract_market"] < 720
    assert config.EXTRACT_SECONDS < main.WATCHDOG_SECONDS < 480  # every other extract: past its budget, inside the DAG's timeout
