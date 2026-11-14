"""mart.job_status counts skills and CV coverage only for your jobs (in_scope). With the website's 12,600 other jobs in
core.job_skill, the view built the coverage of all 13,027 jobs (429,000 rows) and then kept 586: 4 to 53 seconds a read
on a busy warehouse, on every tracker page (2026-11-14)."""

from pathlib import Path

import psycopg
import pytest

from job_radar import db

SCHEMA = (Path(__file__).resolve().parent.parent / "sql" / "schema.sql").read_text(encoding="utf-8")
VIEW = SCHEMA[SCHEMA.index("CREATE VIEW mart.job_status AS"):]
VIEW = VIEW[:VIEW.index("WHERE j.in_scope;")]


def test_the_view_reads_the_skills_of_your_jobs_only():
    assert VIEW.count("IN (SELECT job_id FROM core.job WHERE in_scope)") == 2  # the skill count and the CV coverage


def test_a_job_keeps_its_skills_and_a_job_out_of_scope_stays_out():
    try:
        conn = db.connect()
    except (KeyError, psycopg.OperationalError) as e:
        pytest.skip(f"no warehouse: {e!r}"[:120])
    with conn:
        skill = conn.execute("SELECT skill FROM core.skill WHERE track IS NOT NULL ORDER BY skill LIMIT 1").fetchone()
        if skill is None:
            pytest.skip("no tracked skill in the test database")
        ids = [conn.execute(
            "INSERT INTO core.job (job_key, title, company, location, place, source, job_url, role_rank, role, target_company,"
            " first_seen, in_scope) VALUES (%s, 'Data Engineer', 'Zz View', 'Cairo', 'Egypt', 'manual', %s, 1, 'Data Engineer',"
            " false, current_date, %s) RETURNING job_id", (f"zz view {scope}", f"https://test.example/view-{scope}", scope)).fetchone()[0]
            for scope in (True, False)]
        for i in ids:
            conn.execute("INSERT INTO core.job_skill (job_id, skill) VALUES (%s, %s)", (i, skill[0]))
        rows = conn.execute("SELECT job_id, skill_matches, skills_matched FROM mart.job_status WHERE job_id = ANY(%s)", (ids,)).fetchall()
        conn.rollback()
    assert rows == [(ids[0], 1, skill[0])]
