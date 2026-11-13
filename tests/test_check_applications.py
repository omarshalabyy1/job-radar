"""check_applications: links each application email to the job you applied to by its company and title, moves that job's
status up from it, and changes nothing on a second run (job_radar/check_applications.py)."""

import psycopg
import pytest

from job_radar import db
from job_radar.check_applications import check_applications


@pytest.fixture
def conn():
    try:
        conn = db.connect()
    except (KeyError, psycopg.OperationalError) as e:
        pytest.skip(f"no warehouse: {e!r}"[:120])
    yield conn
    conn.rollback()
    conn.execute("DELETE FROM core.application_email WHERE message_id LIKE '<zz-check-%'")
    conn.execute("DELETE FROM core.application WHERE job_id IN (SELECT job_id FROM core.job WHERE job_key LIKE 'zz check%')")
    conn.execute("DELETE FROM core.job WHERE job_key LIKE 'zz check%'")
    conn.commit()
    conn.close()


def job(conn, title: str, status: str) -> int:
    job_id = conn.execute("INSERT INTO core.job (job_key, title, company, location, place, source, job_url, role_rank, role,"
                          " target_company, first_seen) VALUES (%s, %s, 'Zz Check', 'Cairo', 'Egypt', 'manual', %s, 1,"
                          " 'Data Engineer', false, current_date) RETURNING job_id",
                          (f"zz check {title}", title, f"https://test.example/{title}")).fetchone()[0]
    conn.execute("INSERT INTO core.application (job_id, status, note) VALUES (%s, %s, 'by you')", (job_id, status))
    return job_id


def email(conn, n: int, kind: str, title: str | None, days_ago: int, job_id: int | None = None) -> None:
    conn.execute("INSERT INTO core.application_email (message_id, received_at, sender, subject, company, title, kind, job_id)"
                 " VALUES (%s, now() - make_interval(days => %s), 'hr@zz.example', 'news', 'ZZ CHECK', %s, %s, %s)",
                 (f"<zz-check-{n}>", days_ago, title, kind, job_id))


def state(conn) -> list[tuple]:
    return conn.execute("SELECT j.title, a.status, a.updated_at FROM core.application a JOIN core.job j USING (job_id)"
                        " WHERE j.job_key LIKE 'zz check%' ORDER BY 1").fetchall()


def test_links_by_title_moves_the_status_up_and_a_rerun_changes_nothing(conn):
    engineer = job(conn, "Data Engineer", "applied")
    analyst = job(conn, "BI Analyst", "applied")
    email(conn, 1, "applied", "data engineer", 9)
    email(conn, 2, "interview", "Data Engineer", 3)  # a different spelling: still the same job
    email(conn, 3, "rejected", None, 1)  # names no job: shown on the Overview, never linked or acted on
    conn.commit()
    check_applications(conn)
    links = dict(conn.execute("SELECT message_id, job_id FROM core.application_email WHERE message_id LIKE '<zz-check-%'").fetchall())
    assert links == {"<zz-check-1>": engineer, "<zz-check-2>": engineer, "<zz-check-3>": None}
    assert [(t, s) for t, s, _ in state(conn)] == [("BI Analyst", "applied"), ("Data Engineer", "interview")]
    before = state(conn)
    check_applications(conn)
    assert state(conn) == before
    assert analyst  # untouched


def test_an_email_linked_before_is_left_alone(conn):
    """A link you corrected by hand stays: only unlinked emails are linked and acted on."""
    engineer = job(conn, "Data Engineer", "applied")
    analyst = job(conn, "BI Analyst", "applied")
    email(conn, 4, "rejected", "Data Engineer", 2, job_id=analyst)  # linked to another job, on purpose
    conn.commit()
    check_applications(conn)
    assert [(t, s) for t, s, _ in state(conn)] == [("BI Analyst", "applied"), ("Data Engineer", "applied")]
    assert engineer
