"""A second transform on the same day changes nothing for the website's jobs. On 2026-11-14, the first day website-only jobs
were 5 days old, transform deleted 672 of them as aged; their boards still listed 391, whose keys are not remembered
(core.job_seen is for your jobs), so the next transform stored them again as new: core.job 14684 -> 15075 between two
rounds of scripts/idempotency_check.py. A website-only job now ages from when its board last listed it."""

import pytest

from job_radar import steps
from tests.test_steps import RolledBack, conn  # noqa: F401  (the rolled-back warehouse connection)

URL = "https://test.example/zz-rerun"


def website_job(conn, monkeypatch, listed_days_ago: int) -> None:  # noqa: F811
    """A tech job that is not one of your roles, first seen 10 days ago, last listed `listed_days_ago` days ago."""
    monkeypatch.setitem(steps.FEATURES, "market", True)
    conn.execute("INSERT INTO raw.job_posting (run_date, source, title, company, location, searched_for, job_url, payload,"
                 " last_seen) VALUES (current_date, 'test', 'Backend Software Engineer', 'Zzrerunco', 'Berlin', NULL, %s, '{}',"
                 " current_date)", (URL,))
    steps.transform(RolledBack(conn))
    assert conn.execute("SELECT in_scope FROM core.job WHERE job_url = %s", (URL,)).fetchone() == (False,)
    conn.execute("UPDATE core.job SET first_seen = current_date - 10, last_seen = current_date - %s WHERE job_url = %s",
                 (listed_days_ago, URL))
    conn.execute("UPDATE raw.job_posting SET last_seen = current_date - %s WHERE job_url = %s", (listed_days_ago, URL))


def state(conn):  # noqa: F811
    return conn.execute("SELECT first_seen FROM core.job WHERE job_url = %s", (URL,)).fetchall()


def test_a_website_job_its_board_still_lists_is_kept_and_a_rerun_changes_nothing(conn, monkeypatch):  # noqa: F811
    website_job(conn, monkeypatch, listed_days_ago=0)
    steps.transform(RolledBack(conn))
    first = state(conn)
    steps.transform(RolledBack(conn))
    assert first == state(conn) and len(first) == 1  # not deleted and stored again as new
    assert conn.execute("SELECT first_seen = current_date - 10 FROM core.job WHERE job_url = %s", (URL,)).fetchone() == (True,)


def test_a_website_job_no_board_lists_any_more_leaves_and_stays_out(conn, monkeypatch):  # noqa: F811
    website_job(conn, monkeypatch, listed_days_ago=steps.KEEP_DAYS + 1)
    conn.execute("INSERT INTO raw.job_posting (run_date, source, title, company, location, searched_for, job_url, payload,"
                 " last_seen) VALUES (current_date, 'test', 'Data Engineer', 'Zzrerunother', 'Cairo', 'Egypt',"
                 " 'https://test.example/zz-rerun-other', '{}', current_date)")  # transform refuses a window with no posting
    steps.transform(RolledBack(conn))
    first = state(conn)
    steps.transform(RolledBack(conn))
    assert first == state(conn) == []
