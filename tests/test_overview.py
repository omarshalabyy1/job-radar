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
