"""The tracker's Overview: one row per company you applied to, its stage, and the counts (tracker/overview.py)."""

from datetime import datetime, timedelta, timezone

from tracker.overview import STAGES, applications, board, counts, email_kind, in_range

NOW = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)


def day(n: int) -> datetime:
    """n days before NOW."""
    return NOW - timedelta(days=n)


def mail(company, kind, at, title="Data Engineer", subject="", action=None, sender="jobs@acme.com"):
    return (company, title, kind, at, subject, action, sender)


def status(company, value, at, title="Data Engineer", source="wuzzuf", attempt=None, url="https://x/1"):
    return (company, title, value, at, source, attempt, url)


def test_email_kind_splits_assessments():
    assert email_kind("interview", "Action Required - Complete Assessment", None) == "assessment"
    assert email_kind("interview", "Next Step: Complete Your 2-Minute Video Application", None) == "assessment"
    assert email_kind("interview", "Your invite", "Take the English exam on HackerRank") == "assessment"
    assert email_kind("interview", "Interview with Henkel", None) == "interview"
    assert email_kind("rejected", "Your test results", None) == "rejected"  # only interviews are split


def test_board_precedence():
    assert board("linkedin", "wuzzuf", None) == "LinkedIn"  # the apply attempt says where it went
    assert board("other", "apify:linkedin_remote", None) == "LinkedIn"
    assert board(None, "email", "LinkedIn <jobs-noreply@linkedin.com>") == "LinkedIn"
    assert board(None, "manual:wuzzuf-keywords", None) == "Wuzzuf"
    assert board(None, "thndr", None) == "thndr"
    assert board(None, None, None) == ""  # blank rather than guessed


def test_rejection_after_interview_is_rejected():
    [app] = applications([mail("Acme", "applied", day(20)), mail("Acme", "interview", day(10)),
                          mail("Acme", "rejected", day(5))], [])
    assert app["stage"] == "rejected"


def test_interview_after_rejection_is_interview():
    [app] = applications([mail("Acme", "applied", day(20)), mail("Acme", "rejected", day(10)),
                          mail("Acme", "interview", day(5))], [])
    assert app["stage"] == "interview"


def test_offer_beats_everything():
    [app] = applications([mail("Acme", "interview", day(10)), mail("Acme", "offer", day(5)), mail("Acme", "rejected", day(1))], [])
    assert app["stage"] == "offer"


def test_status_only_company_counts():
    [app] = applications([], [status("Thndr", "applied", day(1), source="thndr")])
    assert (app["company"], app["stage"], app["board"], app["applied_on"]) == ("Thndr", "waiting", "thndr", day(1))
    assert app["job_url"] == "https://x/1"


def test_your_status_and_the_mail_are_one_company():
    apps = applications([mail("ACME Labs", "applied", day(3), sender="LinkedIn <jobs@linkedin.com>",
                              subject="Your application was sent to ACME Labs")],
                        [status("Acme Labs", "interview", day(1))])
    assert len(apps) == 1
    assert apps[0]["stage"] == "interview"
    assert apps[0]["latest_email"] == "Your application was sent to ACME Labs"
    assert apps[0]["applied_on"] == day(3)


def test_email_without_company_groups_by_title():
    apps = applications([mail(None, "applied", day(4), title="BI Developer"), mail("", "rejected", day(2), title="BI Developer")], [])
    assert len(apps) == 1
    assert apps[0]["stage"] == "rejected"
    assert apps[0]["company"] == ""


def test_viewed_only_is_not_an_application():
    assert applications([mail("Acme", "viewed", day(2))], []) == []


def test_counts_add_up():
    apps = applications([mail("A", "applied", day(9)), mail("B", "applied", day(8)), mail("B", "viewed", day(7)),
                         mail("C", "applied", day(6)), mail("C", "interview", day(5), subject="Complete Assessment"),
                         mail("D", "interview", day(4)), mail("E", "applied", day(3)), mail("E", "rejected", day(2))],
                        [status("F", "offer", day(1))])
    n = counts(apps)
    assert n == {"applied": 6, "responses": 5, "waiting": 1, "viewed": 1, "assessment": 1, "interview": 1, "rejected": 1, "offer": 1}
    assert sum(n[s] for s in STAGES) == n["applied"]


def test_counts_of_nothing_are_zeros():
    assert counts([]) == {"applied": 0, "responses": 0, **{s: 0 for s in STAGES}}


def test_in_range_by_latest_activity():
    apps = applications([mail("Old", "applied", day(60)), mail("Old", "rejected", day(2)), mail("Older", "applied", day(45))], [])
    assert [a["company"] for a in in_range(apps, 30, NOW)] == ["Old"]
    assert len(in_range(apps, None, NOW)) == 2


def test_the_overview_queries_and_page_run_on_the_warehouse_schema():
    """The two queries run, and the page shows one interview at a company with only an interview email and your status."""
    from pathlib import Path

    import psycopg
    import pytest
    from streamlit.testing.v1 import AppTest

    from job_radar import db
    from tracker.overview import MAIL, STATUSES
    try:
        conn = db.connect()
    except (KeyError, psycopg.OperationalError) as e:
        pytest.skip(f"no warehouse: {e!r}"[:120])
    with conn:
        job = conn.execute("INSERT INTO core.job (job_key, title, company, location, place, source, job_url, role_rank, role,"
                           " target_company, first_seen) VALUES ('zz overview', 'Data Engineer', 'Zz Overview', 'Cairo', 'Egypt',"
                           " 'apify:linkedin', 'https://test.example/zz-overview', 1, 'Data Engineer', false, current_date)"
                           " RETURNING job_id").fetchone()[0]
        conn.execute("INSERT INTO core.application (job_id, status, note) VALUES (%s, 'applied', 'by you')", (job,))
        conn.execute("INSERT INTO core.application_email (message_id, received_at, sender, subject, company, title, kind)"
                     " VALUES ('<zz-overview-1>', now() - interval '1 day', 'hr@zz.example', 'Interview with Zz Overview',"
                     " 'Zz Overview', 'Data Engineer', 'interview')")
        for sql in (MAIL, STATUSES):
            conn.execute(sql).fetchall()
    try:
        app = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "tracker" / "app_pages" / "overview.py"),
                                default_timeout=60)
        app.run()
        assert not app.exception
        shown = {m.label: m.value for m in app.metric}
        assert (shown["Applied"], shown["Interview"], shown["Waiting"]) == ("1", "1", "0")
        assert {"Follow up", "Follow-up replies", "Drafts"} <= {h.value for h in app.subheader}  # the follow-ups sit under it
    finally:
        with db.connect() as conn:
            conn.execute("DELETE FROM core.application_email WHERE message_id = '<zz-overview-1>'")
            conn.execute("DELETE FROM core.application WHERE job_id = %s", (job,))
            conn.execute("DELETE FROM core.job WHERE job_id = %s", (job,))


def test_applying_again_after_a_rejection_is_waiting():
    [app] = applications([mail("Acme", "applied", day(90)), mail("Acme", "rejected", day(60)), mail("Acme", "applied", day(2))], [])
    assert app["stage"] == "waiting"


def test_each_job_at_one_company_is_its_own_application():
    """Henkel, as the warehouse had it on 2026-11-13: one interview, one rejection, the rest waiting."""
    apps = applications(
        [mail("Henkel", "applied", day(100), title="Data Analytics Engineer - Supply Chain"),
         mail("Henkel", "interview", day(99), title=None, subject="Interview with Henkel"),
         mail("Henkel", "rejected", day(5), title="Data Analytics Engineer - Supply Chain"),
         mail("Henkel", "applied", day(4), title="Jr Data Engineer")],
        [status("Henkel", "interview", day(1), title="Data Analytics Engineer"),
         status("Henkel", "applied", day(0), title="Jr Data Engineer")])
    assert sorted((a["job"], a["stage"]) for a in apps) == [
        ("Data Analytics Engineer", "interview"), ("Data Analytics Engineer - Supply Chain", "rejected"),
        ("Jr Data Engineer", "waiting")]
    assert counts(apps)["applied"] == 3


def test_an_email_naming_no_job_goes_to_the_latest_application_sent_before_it():
    apps = applications([mail("Acme", "applied", day(30), title="BI Developer"), mail("Acme", "applied", day(10), title="Data Engineer"),
                         mail("Acme", "interview", day(5), title=None), mail("Acme", "applied", day(2), title="AI Engineer")], [])
    assert {a["job"]: a["stage"] for a in apps} == {"BI Developer": "waiting", "Data Engineer": "interview", "AI Engineer": "waiting"}


def test_an_email_naming_no_job_before_any_application_goes_to_the_first():
    [app] = applications([mail("Acme", "viewed", day(9), title=None), mail("Acme", "applied", day(5), title="Data Engineer")], [])
    assert app["stage"] == "waiting"  # the view came before you applied (the rule for applying again)


def test_receipt_and_tracker_flags_for_the_checks():
    apps = applications([mail("Mailonly", "applied", day(3))], [status("Statusonly", "applied", day(2)),
                                                                status("Both", "applied", day(2))])
    apps += applications([mail("Both", "applied", day(3))], [])
    flags = {a["company"]: (a["receipt"], a["in_tracker"]) for a in apps}
    assert flags["Mailonly"] == (True, False)
    assert flags["Statusonly"] == (False, True)
