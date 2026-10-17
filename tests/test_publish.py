"""publish retries a Neon deadlock or serialization failure, and gives up after RETRIES."""

import psycopg
import pytest

from job_radar import publish


def run(monkeypatch, errors):
    """publish() with publish_once raising each of `errors` in turn, then succeeding; returns the attempts made."""
    calls = []

    def once(conn):
        calls.append(conn)
        if len(calls) <= len(errors):
            raise errors[len(calls) - 1]

    monkeypatch.setattr(publish, "publish_once", once)
    monkeypatch.setattr(publish.time, "sleep", lambda s: None)
    publish.publish("conn")
    return len(calls)


def test_retries_deadlock_and_serialization(monkeypatch, capsys):
    assert run(monkeypatch, [psycopg.errors.DeadlockDetected("x"), psycopg.errors.SerializationFailure("y")]) == 3
    assert capsys.readouterr().out.count("WARNING publish: Neon rolled back") == 2


def test_gives_up_after_retries(monkeypatch):
    with pytest.raises(psycopg.errors.DeadlockDetected):
        run(monkeypatch, [psycopg.errors.DeadlockDetected("x")] * (publish.RETRIES + 1))


def test_other_errors_not_retried(monkeypatch):
    with pytest.raises(RuntimeError):
        run(monkeypatch, [RuntimeError("no url")])
