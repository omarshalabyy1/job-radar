"""The Overview: one row per job you applied to, its stage, and the counts (job_radar/overview.py)."""

from datetime import date, datetime, timedelta, timezone

from job_radar.overview import STAGES, applications, board, counts, email_kind, in_range

NOW = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)


def day(n: int) -> datetime:
    """n days before NOW."""
    return NOW - timedelta(days=n)


def mail(company, kind, at, title="Data Engineer", subject="", action=None, sender="jobs@acme.com", message_id=None, job_id=None,
         body=None, mailbox="me@gmail.com", done=False):
    return (company, title, kind, at, subject, action, sender, message_id or f"<{company}-{kind}-{at:%j}>", job_id, body, mailbox, done)


def status(company, value, at, title="Data Engineer", source="wuzzuf", attempt=None, url="https://x/1", job_id=1, note=None,
           deadline=None):
    return (company, title, value, at, source, attempt, url, job_id, note, deadline)


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
    # B was only viewed: a view is not a reply, it waits like A (Omar, 2026-11-14)
    assert n == {"applied": 6, "responses": 4, "waiting": 2, "assessment": 1, "interview": 1, "rejected": 1, "offer": 1}
    assert "viewed" not in STAGES
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
    from job_radar.overview import MAIL, STATUSES
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
        assert any("no confirmation email yet" in m.value for m in app.markdown)  # the Checks
        assert "Needs you" in [h.value for h in app.subheader]
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


def test_a_reply_with_a_reworded_title_goes_to_the_application():
    """Valeo: applied to "Graduate In Training Program (GIT), Business Intelligence", invited for
    "Graduate In Training (GIT) - Business Intelligence"."""
    [app] = applications([mail("Valeo", "applied", day(200), title="Graduate In Training Program (GIT), Business Intelligence"),
                          mail("Valeo", "interview", day(8), title="Graduate In Training (GIT) - Business Intelligence")], [])
    assert app["stage"] == "interview"


def test_a_confirmation_with_a_shorter_title_joins_your_status():
    [app] = applications([mail("IBM", "applied", day(1), title="Data Engineer")],
                         [status("IBM", "applied", day(1), title="Data & Analytics Data Engineer - Data Platform")])
    assert (app["receipt"], app["in_tracker"]) == (True, True)


def test_a_confirmation_naming_no_company_joins_the_one_job_with_that_title():
    [app] = applications([mail(None, "applied", day(1), title="Big Data Engineer")],
                         [status("Muller's Solutions", "applied", day(1), title="Big Data Engineer")])
    assert (app["company"], app["receipt"]) == ("Muller's Solutions", True)


def test_emails_matched_by_title_and_not_yet_linked_are_to_link():
    """check_applications links these to the job: matched by title (exact, reworded or shorter), not by "latest before"."""
    [app] = applications([mail("Acme", "applied", day(9), title="data engineer", message_id="<a>"),
                          mail("Acme", "interview", day(5), title="Data Engineer", message_id="<b>", job_id=7),
                          mail("Acme", "rejected", day(2), title=None, message_id="<c>")],
                         [status("Acme", "applied", day(9), job_id=7)])
    assert app["job_id"] == 7
    assert app["to_link"] == [("<a>", "applied", day(9))]  # <b> is linked already, <c> names no job


def test_a_job_you_have_no_status_for_has_nothing_to_link():
    [app] = applications([mail("Acme", "applied", day(9), message_id="<a>")], [])
    assert (app["job_id"], app["to_link"]) == (None, [])


def test_a_reply_from_before_you_applied_is_not_linked():
    """Cartona: an April rejection ("Data Analyst") is not about the Risk Data Analyst job applied to in November."""
    [app] = applications([mail("Cartona", "rejected", day(200), title="Data Analyst", message_id="<old>")],
                         [status("Cartona", "applied", day(1), title="Risk Data Analyst", job_id=5)])
    assert (app["stage"], app["to_link"]) == ("waiting", [])


def test_an_old_confirmation_naming_no_job_is_its_own_application():
    """Cartona: applied in April (no title), rejected in April; the November jobs are new applications."""
    apps = applications([mail("Cartona", "applied", day(210), title=None, message_id="<apr>"),
                         mail("Cartona", "rejected", day(206), title="Data Analyst", message_id="<rej>")],
                        [status("Cartona", "applied", day(1), title="Risk Data Analyst", job_id=5)])
    assert sorted((a["job"], a["stage"], len(a["to_link"])) for a in apps) == [("Data Analyst", "rejected", 0), ("Risk Data Analyst", "waiting", 0)]


def test_the_email_body_tells_an_assessment():
    """IBM's "Your IBM Application: Next Steps" says so only in its body."""
    assert email_kind("interview", "Your IBM Application: Next Steps", None,
                      "Please complete the online assessment within 7 days.") == "assessment"
    assert email_kind("interview", "Interview invite", None, "We will test the call link before we start.") == "interview"


def test_the_newest_of_interview_and_assessment_is_the_stage():
    [ibm] = applications([mail("IBM", "applied", day(40)), mail("IBM", "interview", day(30)),
                          mail("IBM", "interview", day(0), subject="Next Steps", body="complete the assessment")], [])
    assert ibm["stage"] == "assessment"
    [hh] = applications([mail("HH", "applied", day(40)), mail("HH", "interview", day(30), subject="Complete the coding assessment"),
                         mail("HH", "interview", day(1), subject="Interview invite")], [])
    assert hh["stage"] == "interview"


def test_your_note_is_the_latest_news_when_it_is_newer_than_the_email():
    [app] = applications([mail("Henkel", "applied", day(4), subject="Your application for the position Data Engineer")],
                         [status("Henkel", "interview", day(1), note="applied 2026-11-09 (Henkel req 26097659); HR screening interview, told by Omar 2026-11-12")])
    assert app["latest_email"] == "HR screening interview, told by Omar 2026-11-12"
    [machine] = applications([mail("Kake", "applied", day(4), subject="Thanks for applying")],
                             [status("Kake", "rejected", day(1), note="applied by you 2026-11-09; from your mailbox 2026-11-12")])
    assert machine["latest_email"] == "Thanks for applying"  # a rejected status: the email, not the machine note


def test_a_due_date_only_when_the_email_states_one():
    from job_radar.overview import due_date
    assert due_date("Your invite to Valeo English Exam - Speaking expires in 1 days", day(7)) == (day(7) + timedelta(days=1)).date()
    assert due_date("Complete Hallo language assessment from TP (5 days left)", day(3)) == (day(3) + timedelta(days=5)).date()
    assert due_date("Please complete it within 48 hours.", day(1)) == (day(1) + timedelta(hours=48)).date()
    assert due_date("Your IBM Application: Next Steps", day(0)) is None


def test_an_assessment_carries_the_due_date_of_its_newest_email():
    [app] = applications([mail("Valeo", "applied", day(9)),
                          mail("Valeo", "interview", day(7), subject="Your invite to Valeo English Exam - Speaking expires in 1 days")], [])
    assert (app["stage"], app["due"]) == ("assessment", (day(7) + timedelta(days=1)).date())


def test_a_calendar_invitation_is_due_on_its_day():
    from job_radar.overview import due_date
    assert due_date("Invitation: Omar Sabry - BI Developer @ Thu Oct 29, 2026 2pm - 2:30pm (GMT+2)", day(17)) == date(2026, 10, 29)


def test_needs_you_leaves_out_what_is_past():
    from job_radar.overview import needs_you
    apps = applications([mail("Valeo", "interview", day(7), subject="English Exam expires in 1 days"),
                         mail("IBM", "interview", day(1), subject="Next Steps", body="complete the assessment"),
                         mail("HH", "interview", day(2), subject="Invitation: Call @ Mon Nov 16, 2026 2pm")], [])
    assert [a["company"] for a in needs_you(apps, NOW)] == ["HH", "IBM"]  # Valeo's exam expired; due first, then undated


def test_every_application_links_to_its_newest_email_in_its_mailbox():
    from job_radar.overview import gmail_link
    assert gmail_link("me@gmail.com", "<abc.1@mail.x>") == "https://mail.google.com/mail/u/?authuser=me%40gmail.com#search/rfc822msgid%3Aabc.1%40mail.x"
    [app] = applications([mail("Hire Hangar", "interview", day(1), message_id="<v@hh>", mailbox="b@gmail.com")], [])
    assert app["email_link"] == gmail_link("b@gmail.com", "<v@hh>")
    assert app["link"] == app["email_link"]  # no job page: the email is the link
    [job] = applications([mail("IBM", "applied", day(1), message_id="<i@ibm>")], [status("IBM", "applied", day(1), url="https://ibm.example/1")])
    assert (job["link"], job["email_link"]) == ("https://ibm.example/1", gmail_link("me@gmail.com", "<i@ibm>"))


def test_a_placeholder_job_link_falls_back_to_the_email():
    """Henkel's manual row has job_url 'mailto:application-sent-to-henkel-26097659': not a page."""
    [app] = applications([mail("Henkel", "applied", day(3), message_id="<h@henkel>")],
                         [status("Henkel", "interview", day(1), url="mailto:application-sent-to-henkel-26097659")])
    assert app["link"] == app["email_link"]


def test_no_news_14_days_after_an_interview_is_a_rejection():
    """Teleperformance: interview on 29 Oct (the invitation's day), nothing since: ghosted, which Omar counts as rejected."""
    tp = [mail("Teleperformance", "interview", day(17), title="BI Developer",
               subject="Invitation: Omar Sabry - BI Developer @ Thu Oct 29, 2026 2pm - 2:30pm (GMT+2)")]
    [ghosted] = applications(tp, [], now=NOW)
    assert ghosted["stage"] == "rejected" and "ghosted" in ghosted["latest_email"]
    [recent] = applications([mail("IBM", "interview", day(13), subject="Interview invite")], [], now=NOW)
    assert recent["stage"] == "interview"  # 13 days: not yet
    assert applications(tp, [])[0]["stage"] == "interview"  # without a clock, nothing is ghosted


# ---- one application, one row (the 159 "Applied" of 2026-11-14 held about 36 double counts) -----------------------------

def test_a_confirmation_that_came_before_you_set_the_status_joins_that_application():
    """HCLTech: LinkedIn's "your application was sent to HCLTech" (no title) on the 13th, the status set on the 14th."""
    apps = applications([mail("HCLTech", "applied", day(1), title=None, subject="Omar, your application was sent to HCLTech")],
                        [status("HCLTech", "applied", day(0), title="Data Engineer")])
    assert [(a["job"], a["receipt"], a["in_tracker"]) for a in apps] == [("Data Engineer", True, True)]


def test_confirmations_naming_no_job_spread_over_the_applications_without_one():
    """Cartona: two untitled confirmations on the day of two tracker applications; the April application keeps its own."""
    apps = applications([mail("Cartona", "applied", day(200), title=None, message_id="<apr>"),
                         mail("Cartona", "rejected", day(196), title="Data Analyst", message_id="<rej>"),
                         mail("Cartona", "applied", day(1), title=None, message_id="<n1>"),
                         mail("Cartona", "applied", day(1) + timedelta(minutes=5), title=None, message_id="<n2>")],
                        [status("Cartona", "applied", day(0), title="Risk Data Analyst", job_id=5),
                         status("Cartona", "applied", day(0), title="Growth Data Analyst", job_id=6)])
    assert sorted((a["job"], a["stage"], a["receipt"]) for a in apps) == [
        ("Data Analyst", "rejected", True), ("Growth Data Analyst", "waiting", True), ("Risk Data Analyst", "waiting", True)]


def test_two_confirmations_of_one_application_are_one_row():
    """AIS: Workable sends "Thanks for applying to AIS" (no title) and "Data Scientist - AIS" seconds apart."""
    apps = applications([mail("AIS", "applied", day(1), title=None, message_id="<a>"),
                         mail("AIS", "applied", day(1) + timedelta(seconds=20), title="Data Scientist", message_id="<b>")], [])
    assert [(a["company"], a["job"]) for a in apps] == [("AIS", "Data Scientist")]


def test_a_company_under_a_longer_or_accented_name_is_one_company():
    """Advansys / Advansys ME, Mondia / Mondia Media, Systems Limited / Systems Limited - Egypt, Müller`s / Muller's."""
    apps = applications([mail("Mondia", "applied", day(1), title="Agentic AI Engineer"),
                         mail("Müller`s Solutions", "applied", day(1), title="Big Data Engineer")],
                        [status("Mondia Media", "applied", day(0), title="Agentic AI Engineer"),
                         status("Muller's Solutions", "applied", day(0), title="Big Data Engineer"),
                         status("Systems Limited", "applied", day(0), title="AI / Data Engineer", job_id=1),
                         status("Systems Limited - Egypt", "applied", day(0), title="AI / Data Engineer", job_id=2)])
    assert sorted(a["job"] for a in apps) == ["AI / Data Engineer", "Agentic AI Engineer", "Big Data Engineer"]
    assert all(a["in_tracker"] for a in apps)


def test_short_names_are_not_merged_by_their_first_letters():
    apps = applications([], [status("TP", "applied", day(0), title="Analyst"), status("TPG Telecom", "applied", day(0), title="Analyst")])
    assert len(apps) == 2


def test_an_indeed_confirmation_naming_no_company_joins_the_nearest_application_with_that_title():
    """"Indeed Application: Data Engineer" (no company) while Bayzat and Medology both have a Data Engineer application."""
    apps = applications([mail(None, "applied", day(1), title="Data Engineer", subject="Indeed Application: Data Engineer"),
                         mail("Bayzat", "applied", day(1), title="Data Engineer", subject="Bayzat - Data Engineer - Acknowledgement")],
                        [status("Medology clinics", "applied", day(1) + timedelta(hours=1), title="Data Engineer")])
    assert sorted((a["company"], a["receipt"]) for a in apps) == [("Bayzat", True), ("Medology clinics", True)]


def test_indeeds_own_confirmation_joins_the_same_days_application_with_that_title():
    """AIS: Workable's "Data Scientist - AIS" and Indeed's "Indeed Application: Data Scientist" (no company) are one
    application, though Monks also has a Data Scientist application from October."""
    apps = applications([mail("Monks", "applied", day(40), title="Data Scientist"),
                         mail("AIS", "applied", day(1), title="Data Scientist"),
                         mail(None, "applied", day(1) + timedelta(minutes=3), title="Data Scientist", subject="Indeed Application: Data Scientist")], [])
    assert sorted(a["company"] for a in apps) == ["AIS", "Monks"]


def test_what_you_told_about_a_rejection_is_its_latest_news():
    """Teleperformance: the interview, the case study and the English test, then no answer, as Omar told it."""
    told = "HR screening interview, then a case study task and an English test, both passed, then no answer: ghosted, told by Omar 2026-11-14"
    [app] = applications([mail("Teleperformance", "interview", day(17), title="BI Developer", subject="Invitation: BI Developer")],
                         [status("Teleperformance", "rejected", day(0), title="BI Developer", note=told)])
    assert (app["stage"], app["latest_email"]) == ("rejected", told)


def test_your_deadline_for_the_job_is_its_due_day_when_the_email_states_none():
    [app] = applications([mail("IBM", "interview", day(1), subject="Your IBM Application: Next Steps", body="complete the assessment")],
                         [status("IBM", "applied", day(2), deadline=date(2026, 11, 20))])
    assert (app["stage"], app["due"]) == ("assessment", date(2026, 11, 20))
    [stated] = applications([mail("Valeo", "interview", day(1), subject="English Exam expires in 3 days")],
                            [status("Valeo", "applied", day(2), deadline=date(2026, 11, 30))])
    assert stated["due"] == (day(1) + timedelta(days=3)).date()  # the email's own date wins


def test_ibms_assessment_invitation_expires_seven_days_after_it_came():
    """IBM's invitation (2026-10-11): "ideally within 3 days of receiving this email. After 7 days, the assessment
    invitation link will expire." The link's expiry is the deadline, not the ideal."""
    from job_radar.overview import due_date
    text = ("Please complete the test in a single session, as soon as possible – ideally within 3 days of receiving this email. "
            "After 7 days, the assessment invitation link will expire.")
    assert due_date(text, day(2)) == (day(2) + timedelta(days=7)).date()
    assert due_date("The link will expire after 24 hours", day(1)) == (day(1) + timedelta(hours=24)).date()


def test_an_assessment_you_marked_done_no_longer_needs_you():
    """IBM's "Next Steps" asked for the accommodation form; Omar sent it (2026-11-14) and the row stayed under Needs you."""
    from job_radar.overview import needs_you
    now = NOW

    def apps(done):
        return applications([mail("IBM", "applied", day(2)),
                             mail("IBM", "interview", day(1), subject="Your IBM Application: Next Steps", action="assessment", done=done)], [], now)
    assert [a["company"] for a in needs_you(apps(False), now)] == ["IBM"]
    assert needs_you(apps(True), now) == [] and apps(True)[0]["stage"] == "assessment"
