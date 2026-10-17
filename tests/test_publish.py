"""publish retries a Neon deadlock or serialization failure, and gives up after RETRIES; clean_url strips an alert link's tokens."""

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


def test_clean_url_naukrigulf_unwraps_to_job_page():
    from urllib.parse import quote
    job = "https://www.naukrigulf.com/data-engineer-jobs-in-cairo-n-cd-1-jid-2?utm_source=mail&mailerId=9"
    login = "https://www.naukrigulf.com/nglogin/user/mailerLogin?conmailer=SECRET&rUrl=" + quote(job, safe="")
    url = "https://ccs-tracking.naukrigulf.com/?data=SECRET&redirect=" + quote(login, safe="")
    assert publish.clean_url(url) == "https://www.naukrigulf.com/data-engineer-jobs-in-cairo-n-cd-1-jid-2"
    assert publish.clean_url("https://ccs-tracking.naukrigulf.com/?data=SECRET") is None


def test_clean_url_indeed_linkedin_and_opaque():
    assert publish.clean_url("https://eg.indeed.com/rc/clk/dl?jk=abc123&tk=SECRET&alid=SECRET&from=ja") == "https://eg.indeed.com/viewjob?jk=abc123"
    assert publish.clean_url("https://www.linkedin.com/jobs/view/42?trk=x&refId=y&trackingId=z&midToken=SECRET") == "https://www.linkedin.com/jobs/view/42"
    assert publish.clean_url("https://boards.greenhouse.io/x/jobs/1?gh_jid=1&utm_source=mail") == "https://boards.greenhouse.io/x/jobs/1?gh_jid=1"
    assert publish.clean_url("https://links.wellfound.com/s/c/OPAQUE/25") is None
