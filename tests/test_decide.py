"""The Applied, Noise and Skip decisions: one click sets the job's status and note in core.application (rolled back here)."""
import pytest

from tracker.decide import NOISE, decide
from test_steps import add_job, conn  # noqa: F401  (conn is the warehouse fixture)


def row(conn, job_id):
    return conn.execute("SELECT status, note FROM core.application WHERE job_id = %s", (job_id,)).fetchone()


def test_applied_sets_the_status_and_keeps_an_earlier_note(conn):
    job = add_job(conn, "decide applied")
    conn.execute("INSERT INTO core.application (job_id, status, note) VALUES (%s, 'saved', 'draft ready 2026-11-01')", (job,))
    decide(conn, job, "applied")
    status, note = row(conn, job)
    assert status == "applied" and note.startswith("draft ready 2026-11-01; applied by you ")


@pytest.mark.parametrize("reason", sorted(NOISE))
def test_noise_ignores_the_job_and_says_why(conn, reason):
    job = add_job(conn, f"decide noise {reason}")
    decide(conn, job, "noise", reason)
    status, note = row(conn, job)
    assert status == "ignored" and note.startswith(f"noise: {NOISE[reason]} (Omar, ")


def test_skip_ignores_the_job_with_the_skipped_note(conn):
    job = add_job(conn, "decide skip")
    decide(conn, job, "skip")
    status, note = row(conn, job)
    assert status == "ignored" and note.startswith("skipped by Omar ")


def test_an_unknown_action_or_reason_changes_nothing(conn):
    job = add_job(conn, "decide unknown")
    with pytest.raises(ValueError):
        decide(conn, job, "delete")
    with pytest.raises(ValueError):
        decide(conn, job, "noise", "because")
    assert row(conn, job) is None
