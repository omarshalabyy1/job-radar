import logging
from unittest.mock import MagicMock

import pytest

import apify_rotation as ar

TOKEN = "apify_test_token"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for n in ar.SLOTS:
        monkeypatch.delenv(f"APIFY_TOKEN_{n}", raising=False)
    monkeypatch.setenv("APIFY_ACCOUNT_LIMIT_USD", "5.00")
    monkeypatch.setenv("APIFY_SAFETY_MARGIN_USD", "0.50")


def client(usage=None, error=None, run=None):
    c = MagicMock()
    if error:
        c.user.return_value.monthly_usage.side_effect = error
    else:
        c.user.return_value.monthly_usage.return_value = {ar.USAGE_FIELD: usage}
    c.actor.return_value.start.return_value = run
    return c


def test_missing_tokens_reported(monkeypatch, caplog):
    monkeypatch.setenv("APIFY_TOKEN_2", TOKEN)
    with caplog.at_level(logging.INFO):
        accounts = ar.load_accounts()
    assert list(accounts) == [2]
    assert "account 1: not configured" in caplog.text
    assert TOKEN not in caplog.text


def test_usage_retrieved():
    r = ar.check(1, client(usage=1.25))
    assert r["usage"] == 1.25 and r["remaining"] == 3.75 and r["eligible"]


def test_usage_failure_ineligible():
    r = ar.check(1, client(error=RuntimeError("boom")))
    assert r["usage"] is None and not r["eligible"]


def test_above_threshold_does_not_start(tmp_path):
    c = client(usage=4.50, run={"id": "x"})
    assert ar.run_actor(1, c, "u/a", None, ar.initialize_db(tmp_path / "s.db")) == 2
    c.actor.return_value.start.assert_not_called()


def test_below_threshold_starts_and_logs(tmp_path):
    db_path = tmp_path / "s.db"
    c = client(usage=1.0, run={"id": "run1", "status": "READY"})
    assert ar.run_actor(3, c, "u/a", {"q": 1}, ar.initialize_db(db_path)) == 0
    c.actor.return_value.start.assert_called_once_with(run_input={"q": 1})
    rows = ar.initialize_db(db_path).execute("SELECT account_number, actor_id, run_id, status FROM runs").fetchall()
    assert rows == [(3, "u/a", "run1", "READY")]


def test_missing_run_id(tmp_path):
    c = client(usage=1.0, run={"status": "READY"})
    assert ar.run_actor(1, c, "u/a", None, ar.initialize_db(tmp_path / "s.db")) == 3


def test_ambiguous_launch_no_retry(tmp_path):
    c = client(usage=1.0)
    c.actor.return_value.start.side_effect = TimeoutError()
    assert ar.run_actor(1, c, "u/a", None, ar.initialize_db(tmp_path / "s.db")) == 3
    assert c.actor.return_value.start.call_count == 1


@pytest.mark.parametrize("text", ["{bad", "[1, 2]"])
def test_invalid_input_file(tmp_path, text):
    p = tmp_path / "in.json"
    p.write_text(text)
    with pytest.raises(SystemExit):
        ar.load_input(p)


def test_token_never_logged(monkeypatch, tmp_path, caplog, capsys):
    monkeypatch.setenv("APIFY_TOKEN_1", TOKEN)
    monkeypatch.setenv("APIFY_STATE_DB", str(tmp_path / "s.db"))
    fake = client(error=RuntimeError(TOKEN))
    monkeypatch.setattr(ar, "ApifyClient", lambda token: fake)
    with caplog.at_level(logging.DEBUG):
        ar.main(["report"])
        ar.main(["run", "1", "u/a"])
    assert TOKEN not in caplog.text + capsys.readouterr().out
