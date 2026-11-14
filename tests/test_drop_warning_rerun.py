"""A second run on the same day does not erase the day's count. On 2026-11-13 hireeing gave 3 postings at noon; the night
run found no new page and wrote 0 over the 3, so the week's history lost the day. The warning itself still judges each run
(tests/test_steps.py pins that: a run that reads nothing warns, whatever an earlier run of the day read)."""

import psycopg
import pytest

from job_radar import db, steps


@pytest.fixture
def conn():
    try:
        conn = db.connect()
    except (KeyError, psycopg.OperationalError) as e:
        pytest.skip(f"no warehouse: {e!r}"[:120])
    yield conn
    conn.rollback()
    conn.execute("DELETE FROM core.extract_count WHERE board LIKE 'zz_rerun%'")
    conn.commit()
    conn.close()


def test_a_quiet_rerun_keeps_the_days_count(conn):
    steps.drop_warning(conn, "zz_rerun", 3, 1)  # the noon run
    steps.drop_warning(conn, "zz_rerun", 0, 0)  # the night run: nothing new
    assert conn.execute("SELECT postings, dropped FROM core.extract_count WHERE board = 'zz_rerun' AND day = current_date").fetchone() == (3, 1)


def test_a_fuller_rerun_replaces_the_days_count(conn):
    steps.drop_warning(conn, "zz_rerun_more", 3, 1)
    steps.drop_warning(conn, "zz_rerun_more", 5, 0)
    assert conn.execute("SELECT postings, dropped FROM core.extract_count WHERE board = 'zz_rerun_more' AND day = current_date").fetchone() == (5, 0)
