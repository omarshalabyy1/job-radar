r"""The tracker's Insights page: each application's furthest stage, and its interview rate by source, role, Claude's score
and the posting's age when you applied. Run on the laptop: .venv\Scripts\python -m pytest tests\test_insights.py
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

from job_radar import db
from job_radar.applications import job_outcomes
from tracker.insights_tab import JOBS, MAIL, STATUSES, age_band, applications, rates

NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)


def at(days_ago: float) -> datetime:
    return NOW - timedelta(days=days_ago)


MAIL_ROWS = [  # company (the matched job's), title, kind, received_at, job_id
    ("Zz Acme", "Data Engineer", "applied", at(20), 1), ("ZZ ACME", None, "interview", at(15), None),
    ("Zz Acme", None, "rejected", at(10), None),                                  # rejected after the interview: interview
    ("Zz Globex", "BI Developer", "applied", at(40), 2), ("Zz Globex", None, "viewed", at(35), None),  # no answer in 30: ghosted
    ("Zz Initech", "Data Analyst", "applied", at(5), 3), ("Zz Initech", None, "viewed", at(4), None),  # viewed
    ("Zz Hooli", "AI Engineer", "applied", at(3), None),                          # matched by your status at that company
    ("Zz Umbrella", "Data Engineer", "applied", at(2), None)]                     # matched to no job: left out
STATUSES_ROWS = [(4, "Zz Hooli", "applied"), (5, "Zz Wonka", "offer"),          # an offer you set, with no email
                 (3, "Zz Initech", "rejected")]                                   # your rejection beats the mailbox's view


def test_each_job_takes_the_furthest_stage_its_company_reached():
    out = job_outcomes(MAIL_ROWS, STATUSES_ROWS, NOW, 30)
    assert {job_id: outcome for job_id, (outcome, _) in out.items()} == {
        1: "interview", 2: "ghosted", 3: "rejected", 4: "applied", 5: "offer"}
    assert out[1][1] == at(20) and out[4][1] == at(3) and out[5][1] is None  # applied_at: the first confirmation


def test_the_age_bands_count_from_the_posting_to_the_first_confirmation():
    assert age_band(at(1), at(3)) == "48 hours or less"
    assert age_band(at(1), at(3.1)) == "2-7 days" and age_band(at(1), at(8)) == "2-7 days"
    assert age_band(at(1), at(8.1)) == "Over 7 days"
    assert age_band(at(2), at(1)) == "48 hours or less"  # applied before the radar's posting time
    assert age_band(None, at(1)) == "Unknown" and age_band(at(1), None) == "Unknown"


def test_the_rate_tables_count_applications_interviews_and_outcomes_per_group():
    jobs = [(1, "wuzzuf", "Data Engineer", at(21), 9), (2, "wuzzuf", "BI Developer", at(50), 6),
            (3, "linkedin", "Data Engineer", at(9), 4), (4, "linkedin", "AI Engineer", None, None),
            (5, "manual", "Data Engineer", at(60), 8)]
    apps = applications(MAIL_ROWS, STATUSES_ROWS, jobs, NOW)
    by_source = rates(apps, "source").set_index("Source")
    assert by_source.loc["wuzzuf", ["Applications", "Interviews", "Interview rate %", "Interview", "Ghosted"]].tolist() == [2, 1, 50.0, 1, 1]
    assert by_source.loc["linkedin", ["Applications", "Interviews", "Interview rate %"]].tolist() == [2, 0, 0.0]
    by_score = rates(apps, "score", ["0-5", "6-7", "8-10", "Not graded"])
    assert by_score["Score"].tolist() == ["0-5", "6-7", "8-10", "Not graded"]  # in band order, not by size
    assert by_score.set_index("Score").loc["8-10", ["Applications", "Interviews"]].tolist() == [2, 2]  # an offer is an interview
    by_age = rates(apps, "age").set_index("Age")["Applications"].to_dict()
    assert by_age == {"48 hours or less": 1, "2-7 days": 1, "Over 7 days": 1, "Unknown": 2}  # no confirmation or no posting time


def test_the_insights_queries_run_on_the_warehouse_schema():
    try:
        conn = db.connect()
    except (KeyError, psycopg.OperationalError) as e:
        pytest.skip(f"no warehouse: {e!r}"[:120])
    with conn:
        for sql in (MAIL, STATUSES, JOBS):
            conn.execute(sql).fetchall()


@pytest.fixture
def one_application(monkeypatch, tmp_path):
    """One graded job you applied to, with its emails, a follow-up draft and a Ready draft, committed to the throwaway
    database (the page opens its own connection) and deleted after the test; the drafts in tmp_path."""
    from job_radar import applications, tailor
    try:
        conn = db.connect()
    except (KeyError, psycopg.OperationalError) as e:
        pytest.skip(f"no warehouse: {e!r}"[:120])
    jobs = [conn.execute(
        "INSERT INTO core.job (job_key, title, company, location, place, source, job_url, role_rank, role, target_company,"
        " first_seen, posted_at) VALUES (%s, %s, 'Zz Pages', 'Cairo', 'Egypt', 'wuzzuf', %s, 1, 'Data Engineer', false,"
        " current_date, now() - make_interval(hours => %s)) RETURNING job_id",
        (f"zz pages {t}", t, f"https://test.example/pages-{t}", hours)).fetchone()[0]
        for t, hours in (("Data Engineer", 30 * 24), ("BI Developer", 2))]  # the draft's job posted 2 hours ago: Apply today
    for job_id in jobs:
        conn.execute("INSERT INTO core.claude_grade (job_id, score, cv, reason, model) VALUES (%s, 9, 'cv', 'why', 'm')", (job_id,))
    conn.execute("INSERT INTO core.application (job_id, status, note) VALUES (%s, 'interview', 'by you'),"
                 " (%s, 'saved', 'draft ready 2026-10-30')", jobs)
    conn.execute("INSERT INTO core.application_email (message_id, received_at, sender, subject, company, title, kind, job_id)"
                 " VALUES ('<zz-pages-1>', now() - interval '20 days', 'hr@zz.example', 'Thanks', 'Zz Pages', 'Data Engineer',"
                 " 'applied', %s), ('<zz-pages-2>', now() - interval '5 days', 'hr@zz.example', 'Interview', 'Zz Pages', NULL,"
                 " 'interview', NULL)", (jobs[0],))
    conn.commit()
    day = (datetime.now(applications.CAIRO) - timedelta(days=13)).date()
    (tmp_path / "followups" / str(day)).mkdir(parents=True)
    (tmp_path / "followups" / str(day) / "zz-pages-data-engineer.md").write_text("Subject: hi\n\nbody", encoding="utf-8")
    draft = tailor.draft_folder("2026-10-30", "Zz Pages", "BI Developer", jobs[1]).relative_to(tailor.OUTPUT)
    (tmp_path / draft).mkdir(parents=True)
    (tmp_path / draft / "cover-letter.md").write_text("Dear team", encoding="utf-8")
    monkeypatch.setattr(applications, "OUTPUT", tmp_path)
    monkeypatch.setattr(tailor, "OUTPUT", tmp_path)
    yield
    conn.execute("DELETE FROM core.application_email WHERE message_id LIKE '<zz-pages-%'")
    conn.execute("DELETE FROM core.application WHERE job_id = ANY(%s)", (jobs,))
    conn.execute("DELETE FROM core.job WHERE job_id = ANY(%s)", (jobs,))  # its grade goes with it
    conn.commit()
    conn.close()


@pytest.mark.parametrize("page", ["insights", "ready", "applications"])
def test_the_changed_tracker_pages_render_their_new_parts(page, one_application):
    """Each page run once by Streamlit's test runner, as the tracker runs it."""
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "tracker" / "app_pages" / f"{page}.py"), default_timeout=60)
    app.run()
    assert not app.exception
    shown = {"insights": ("By source", "Interview rate"), "ready": ("Apply today", None),
             "applications": ("Follow-up replies", "Answered after a follow-up")}[page]
    assert shown[0] in [s.value for s in app.subheader]
    if shown[1]:
        assert shown[1] in [m.label for m in app.metric]
    if page == "insights":
        assert app.metric[0].value == "100.0%"  # the one application reached an interview
    if page == "applications":
        assert app.metric[-1].value == "1 of 1 (100.0%)"  # the interview came after the follow-up's day
