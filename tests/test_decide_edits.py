"""The tables' Decide and Done columns: an edit made in the table is saved the moment it is made (rolled back here)."""
from tracker.decide import DECISIONS, apply_decisions, mark_deadlines_done
from test_steps import add_job, conn  # noqa: F401  (conn is the warehouse fixture)


def row(conn, job_id):
    return conn.execute("SELECT status, note FROM core.application WHERE job_id = %s", (job_id,)).fetchone()


def test_a_picked_decision_is_saved_for_the_job_on_that_row(conn):
    jobs = [add_job(conn, f"edits {n}") for n in range(4)]
    edited = {0: {"decide": "Applied"}, 2: {"decide": "Noise: closed"}, 3: {"decide": "Skip"}, 1: {"status": "saved"}}
    assert apply_decisions(conn, edited, jobs) == 3
    assert row(conn, jobs[0])[0] == "applied"
    assert row(conn, jobs[1]) is None  # a row with no decision is left to the Save button
    assert row(conn, jobs[2]) == ("ignored", row(conn, jobs[2])[1]) and row(conn, jobs[2])[1].startswith("noise: applications closed")
    assert row(conn, jobs[3])[1].startswith("skipped by Omar")


def test_ticking_the_applied_box_marks_the_job_applied_and_unticking_does_nothing(conn):
    jobs = [add_job(conn, f"edits applied {n}") for n in range(2)]
    assert apply_decisions(conn, {0: {"applied": True}, 1: {"applied": False}}, jobs) == 1
    assert row(conn, jobs[0])[0] == "applied" and row(conn, jobs[1]) is None


def test_ticking_the_noise_box_ignores_the_job_as_closed(conn):
    jobs = [add_job(conn, f"edits noise {n}") for n in range(2)]
    assert apply_decisions(conn, {0: {"noise": True}, 1: {"noise": False}}, jobs) == 1
    status, note = row(conn, jobs[0])
    assert status == "ignored" and note.startswith("noise: applications closed (Omar, ") and row(conn, jobs[1]) is None


def test_every_decision_label_is_a_known_action():
    assert set(DECISIONS) == {"Applied", "Noise: closed", "Noise: high exp.", "Noise: intern", "Skip"}


def test_a_ticked_deadline_is_done(conn):
    ids = [conn.execute("INSERT INTO core.deadline (due, what) VALUES (current_date, %s) RETURNING deadline_id", (f"edits {n}",)).fetchone()[0]
           for n in range(2)]
    assert mark_deadlines_done(conn, {1: {"done": True}, 0: {"done": False}}, ids) == 1
    assert conn.execute("SELECT deadline_id, done FROM core.deadline WHERE deadline_id = ANY(%s) ORDER BY deadline_id", (ids,)).fetchall() == [
        (ids[0], False), (ids[1], True)]
