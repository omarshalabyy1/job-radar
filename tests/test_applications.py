"""The plain patterns that tell your application emails apart (applications.classify), on fictional
companies in the shapes the real senders use: an email the patterns misread, or stop reading, fails here. Run on the
laptop: .venv\\Scripts\\python -m pytest tests
"""

from datetime import date

import pytest

from job_radar.applications import classify, find_job

ME = "alex@example.com"
LINKEDIN = "LinkedIn <jobs-noreply@linkedin.com>"

CASES = [
    (LINKEDIN, "Alex, your application was sent to Initech", ("applied", "Initech", None)),
    (LINKEDIN, "Alex, your application was sent to Hooli - a Globex Subsidiary", ("applied", "Hooli - a Globex Subsidiary", None)),
    (LINKEDIN, "Your application was viewed by Acme", ("viewed", "Acme", None)),
    (LINKEDIN, "Your application to Data Engineer at Acme Plus", ("rejected", "Acme Plus", "Data Engineer")),
    ("Workable <noreply@candidates.workablemail.com>", "Thanks for applying to Umbrella MENA", ("applied", "Umbrella MENA", None)),
    ("Acme <notification@smartrecruiters.com>", "​Acme​ - Thank you for your application", ("applied", "Acme", None)),
    ("Acme Hiring Team <notifications@smartrecruiters.com>", "​Acme​ - Thank you for your interest!", ("rejected", "Acme", None)),
    ('"Globex Business @ icims" <globex+autoreply@talent.icims.eu>', "Thank you for applying at Cairo,Egypt: Downtown Office", ("applied", "Globex Business", None)),
    ('"Vandelay @ icims" <vandelay+autoreply@talent.icims.eu>', "Thank you for your application at Vandelay", ("applied", "Vandelay", None)),
    ('"Vandelay @ icims" <vandelay+autoreply@talent.icims.eu>', "Great - you’ve started your application with Vandelay!", ("other", None, None)),
    ("wonka@myworkday.com", "Confirming your Wonka job application", ("applied", "Wonka", None)),
    ("Stark Pay <no-reply@recruitera.ai>", "Thank you for applying to Stark Pay", ("applied", "Stark Pay", None)),
    ("Soylent Group <recruiting@jobalerts.soylent.example>", "Thank you for your application to Soylent Group", ("applied", "Soylent Group", None)),
    ("hr.recruitment@cyberdyne.example", "Your application for the position Platform Reporting Engineer - Logistics at Cyberdyne", ("applied", "Cyberdyne", "Platform Reporting Engineer - Logistics")),
    ("hr.recruitment@cyberdyne.example", "Your application for the position Platform Reporting Engineer (Night Shift) at Cyberdyne", ("applied", "Cyberdyne", "Platform Reporting Engineer (Night Shift)")),
    ("HR System <HRsystem@tyrell.example>", "Tyrell Group – New Job Application Received", ("applied", "Tyrell", None)),
    ("Jobs <jobs@acme.example>", "Acme - New Job Application Received", ("applied", "Acme", None)),
    ("Cyberdyne Recruitment <interviews@hirevue.com>", "Interview with Cyberdyne", ("interview", "Cyberdyne", None)),
    ("Sam Lee <sam@recruiter.example>", "AI Interview Invite for Business Analyst(SQL) - Remote", ("interview", None, "Business Analyst(SQL)")),
    ("Alex Example <alex@example.com>", "Application for Junior Reporting Analyst", ("applied", None, "Junior Reporting Analyst")),
    ("Alex Example <alex@example.com>", "Application for Dashboard Developer – Alex Example", ("applied", None, "Dashboard Developer")),
    # not application news
    ("LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>", "Application Support Engineer at Acme Capital", ("other", None, None)),
    ("GitHub <noreply@github.com>", "[GitHub] A new OAuth application has been added to your account", ("other", None, None)),
    ("Reddit <noreply@redditmail.com>", "\"Tableau Interview (3-5 YOE) - rounds, timeline, salary expectations?\"", ("other", None, None)),
]


@pytest.mark.parametrize("sender, subject, expected", CASES)
def test_pattern_classifier(sender, subject, expected):
    assert classify(sender, subject, ME) == expected


@pytest.mark.parametrize("sender, subject", [
    ("HRM System <no-reply@hrm.example>", "Update on Your Application for Platform Engineer"),
    ("Sam Lee <sam@wuzzuf.net>", "Regarding your job application - Reporting Analyst at Acme"),
    ("Someone <someone@example.com>", "Application for Reporting Analyst"),  # not from you: Claude reads it
])
def test_unclear_emails_are_left_for_claude(sender, subject):
    assert classify(sender, subject, ME) is None


def test_find_job_by_key_then_by_only_company():
    seen = date(2026, 10, 1)
    jobs = [(1, "Data Engineer", "Acme Ltd.", seen), (2, "Analyst", "Globex", seen),
            (3, "BI Developer", "GLOBEX", seen), (4, "Data Engineer", "Initech", date(2026, 10, 8)),
            (5, "Forward Deployed AI Engineer, Egypt - BCG X", "BCG X", seen), (6, "Junior AI Engineer", "SourceQS (recruiter)", seen)]
    on = date(2026, 10, 5)
    assert find_job(jobs, "Data Engineer - Cairo", "ACME", on) == 1  # same job_key
    assert find_job(jobs, None, "Acme", on) == 1                      # no title: the only job of the company
    assert find_job(jobs, "BI Analyst", "Acme", on) is None           # another title: not that job
    assert find_job(jobs, None, "Globex", on) is None                 # two jobs: no guess
    assert find_job(jobs, "Data Engineer", "Initech", on) is None     # first seen after the email: a later posting
    assert find_job(jobs, "Data Engineer", "Initech", date(2026, 10, 8)) == 4
    assert find_job(jobs, "Forward Deployed AI Engineer, Egypt - BCG X", "Boston Consulting Group (BCG)", on) == 5  # a bracketed short name
    assert find_job(jobs, "Junior AI Engineer", "SourceQS", on) == 6  # a note after the company


def test_find_job_keeps_a_companys_roles_with_different_notes_apart():
    """2026-11-12: Henkel's rejection of 'Data Analytics Engineer - Supply Chain' marked 'Data Analytics Engineer
    (Evening Shift)' rejected, and the plain 'Data Analytics Engineer' was filed under it too: three requisitions."""
    seen = date(2026, 10, 1)
    jobs = [(1, "Data Analytics Engineer (Evening Shift)", "Henkel", seen), (2, "Data Analytics Engineer", "Henkel", seen),
            (3, "Data Engineer (Hybrid)", "Acme", seen)]
    on = date(2026, 11, 8)
    assert find_job(jobs, "Data Analytics Engineer - Supply Chain", "Henkel", on) is None  # a third role: neither job
    assert find_job(jobs, "Data Analytics Engineer (Evening Shift)", "Henkel", on) == 1
    assert find_job(jobs, "Data Analytics Engineer", "Henkel", on) == 2
    assert find_job(jobs, "Data Engineer - Cairo, Egypt", "Acme", on) == 3  # a place or a work mode is no other role
    assert find_job(jobs, "Data Engineer (Global)", "Acme", on) == 3


def test_switched_off_the_mailbox_is_not_touched(monkeypatch, capsys):
    import imaplib

    from job_radar import applications

    def no_mailbox(*args):
        raise AssertionError("the mailbox was opened")

    monkeypatch.setattr(imaplib, "IMAP4_SSL", no_mailbox)
    for features in ({}, {"track_applications": False}):
        monkeypatch.setitem(applications.SETTINGS, "features", features)
        applications.track_applications(None)  # no connection needed: it returns first
        assert "off in settings.yaml (features.track_applications)" in capsys.readouterr().out


# ---- follow-up drafts, interview prep and the funnel (fictional companies) ----

from datetime import datetime, time, timedelta, timezone  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import anthropic  # noqa: E402
from job_radar import applications  # noqa: E402
from job_radar.applications import FollowUp, Prep, Question, Story, application_stages, channel, due_follow_ups, funnel_counts  # noqa: E402

TODAY = date(2026, 10, 20)


def at(days_ago: int) -> datetime:
    return datetime(2026, 10, 20, 9, 30, tzinfo=timezone.utc) - timedelta(days=days_ago)


def test_follow_up_is_due_on_exactly_day_7_and_day_14():
    rows = [("Acme", "Data Engineer", "applied", at(7)), ("Globex", None, "applied", at(14)),
            ("Initech", None, "applied", at(6)), ("Hooli", None, "applied", at(8)), ("Umbrella", None, "applied", at(15)),
            ("Vandelay", None, "applied", at(21))]
    assert [(c, t, d) for c, t, _, d in due_follow_ups(rows, TODAY)] == [("Globex", None, 14), ("Acme", "Data Engineer", 7)]


def test_follow_up_counts_from_the_first_confirmation_and_waits_for_no_answer():
    rows = [("Acme", None, "applied", at(7)), ("Acme", None, "applied", at(1)),  # a second confirmation does not restart the clock
            ("Globex", None, "applied", at(7)), ("GLOBEX Ltd", None, "applied", at(3)),
            ("Hooli", None, "applied", at(7)), ("Hooli", None, "rejected", at(2)),
            ("Initech", None, "applied", at(14)), ("Initech", None, "interview", at(10)),
            ("Soylent", None, "applied", at(7)), ("Soylent", None, "viewed", at(3)),  # viewed is not an answer
            ("Tyrell", None, "applied", at(7)), ("Tyrell", None, "rejected", at(9)),  # an answer before applying is not an answer
            (None, "Data Analyst", "interview", at(2)), ("Wonka", "Data Analyst", "applied", at(7)),  # the email names no company: its title
            (None, "Junior Analyst", "applied", at(7))]  # nobody to write to
    assert sorted(c for c, *_ in due_follow_ups(rows, TODAY)) == ["Acme", "Globex", "Soylent", "Tyrell"]


def test_follow_up_day_is_the_cairo_calendar_day():
    late = datetime(2026, 10, 13, 22, 0, tzinfo=timezone.utc)  # 01:00 on the 14th in Cairo (UTC+3 in October)
    assert due_follow_ups([("Acme", None, "applied", late)], date(2026, 10, 21)) == [("Acme", None, date(2026, 10, 14), 7)]
    assert due_follow_ups([("Acme", None, "applied", late)], date(2026, 10, 20)) == []  # 6 days in Cairo, 7 by the UTC date
    elsewhere = late.astimezone(timezone(timedelta(hours=-5)))  # the same instant in another zone: the same Cairo day
    assert due_follow_ups([("Acme", None, "applied", elsewhere)], date(2026, 10, 21))[0][2] == date(2026, 10, 14)
    early = datetime(2026, 10, 14, 20, 59, tzinfo=timezone.utc)  # 23:59 on the 14th in Cairo, still the 14th
    assert due_follow_ups([("Acme", None, "applied", early)], date(2026, 10, 21))[0][2] == date(2026, 10, 14)


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)


class FakeConn:
    """Answers the few queries the drafts make: one CV, no job, one earlier application, the emails given."""

    def __init__(self, emails=()):
        self.emails = list(emails)

    def execute(self, sql, params=None):
        sql = " ".join(sql.split())
        rows = {"FROM core.application_email e LEFT JOIN core.job j USING (job_id) WHERE e.kind IN": self.emails, "SELECT DISTINCT title": [("Data Engineer",)],
                "FROM core.job": [], "FROM core.cv ORDER": [("Alex Example. Built a pipeline of 3 sources.",)]}
        found = next((r for key, r in rows.items() if key in sql), None)
        assert found is not None, sql
        return Result(found)


class FakeClaude:
    def __init__(self, parsed):
        self.parsed, self.calls, self.kwargs = parsed, 0, None
        self.messages = self

    def parse(self, **kwargs):
        self.calls, self.kwargs = self.calls + 1, kwargs
        usage = SimpleNamespace(input_tokens=1000, output_tokens=200, cache_creation_input_tokens=0, cache_read_input_tokens=0)
        return SimpleNamespace(parsed_output=self.parsed, usage=usage)


def switched_on(monkeypatch, tmp_path, claude, **features):
    monkeypatch.setitem(applications.SETTINGS, "features", features)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(applications, "OUTPUT", tmp_path / "output")
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: claude)


GOOD = FollowUp(subject="Data Engineer application", body="Hello Acme team, I built a pipeline of 3 sources. "
                "Could you share an update on my application? Alex Example")


def test_follow_up_is_drafted_once_per_day_and_never_when_switched_off(monkeypatch, tmp_path):
    emails = [("Acme", "Data Engineer", "applied", at(7))]
    claude = FakeClaude(GOOD)
    switched_on(monkeypatch, tmp_path, claude)  # switch missing: off
    assert applications.follow_up_drafts(FakeConn(emails), TODAY) == (0, 0) and claude.calls == 0
    switched_on(monkeypatch, tmp_path, claude, follow_up_drafts=True)
    assert applications.follow_up_drafts(FakeConn(emails), TODAY) == (1, 1) and claude.calls == 1
    file = tmp_path / "output" / "followups" / "2026-10-20" / "acme-data-engineer.md"
    text = file.read_text(encoding="utf-8")
    assert text.startswith("Subject: Data Engineer application\n\nHello Acme team") and "Not sent." in text
    assert applications.follow_up_drafts(FakeConn(emails), TODAY)[0] == 0 and claude.calls == 1  # the hourly rerun: no new call
    assert file.read_text(encoding="utf-8") == text
    # a week later the same application is 14 days old: a draft of its own, in that day's folder
    assert applications.follow_up_drafts(FakeConn(emails), date(2026, 10, 27))[0] == 1 and claude.calls == 2
    assert (tmp_path / "output" / "followups" / "2026-10-27" / "acme-data-engineer.md").exists()


def test_follow_up_that_is_too_long_or_names_a_gap_is_not_written(monkeypatch, tmp_path):
    emails = [("Acme", None, "applied", at(7))]
    for body in ("word " * 121, "I have not yet used Airflow. Could you update me?", "I closed an experience gap in Airflow. Update please."):
        claude = FakeClaude(FollowUp(subject="Update", body=body))
        switched_on(monkeypatch, tmp_path, claude, follow_up_drafts=True)
        assert applications.follow_up_drafts(FakeConn(emails), TODAY)[0] == 0 and claude.calls == 1
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("text, caught", [
    ("I have not yet used Airflow", True), ("We haven't worked with dbt", True), ("We haven’t worked with dbt", True),
    ("No experience with Spark", True), ("A skills gap", True), ("an experience gap", True), ("I lack Spark", True), ("Lacking Airflow", True),
    ("My Slack bot closed the price gap", False), ("Built on Slack and Blackboard", False), ("Closed the gap between plan and actual", False),
    ("Lead times fell 30%", False)])
def test_the_gap_words_are_whole_words_about_the_candidate(text, caught):
    assert (applications.GAP_WORDS.search(text) is not None) is caught


def test_a_draft_may_name_a_slack_bot_and_a_price_gap(monkeypatch, tmp_path):
    claude = FakeClaude(FollowUp(subject="Update", body="I built a Slack bot that closed a price gap. Could you share an update?"))
    switched_on(monkeypatch, tmp_path, claude, follow_up_drafts=True)
    assert applications.follow_up_drafts(FakeConn([("Acme", None, "applied", at(7))]), TODAY)[:2] == (1, 1)


def test_the_day_of_the_drafts_is_the_cairo_day(monkeypatch, tmp_path):
    switched_on(monkeypatch, tmp_path, FakeClaude(GOOD), follow_up_drafts=True)
    monkeypatch.setattr(applications, "cairo_today", lambda: TODAY)
    assert applications.follow_up_drafts(FakeConn([("Acme", None, "applied", at(7))]))[:2] == (1, 1)
    assert (tmp_path / "output" / "followups" / "2026-10-20" / "acme.md").exists()
    assert applications.CAIRO.key == "Africa/Cairo"


def prep(questions=8, stories=3) -> Prep:
    return Prep(role_needs=["Build pipelines"], questions=[Question(question=f"Q{i}?", answer="From the CV.") for i in range(questions)],
                stories=[Story(title=f"S{i}", story="Cut a run from 4 hours to 20 minutes.") for i in range(stories)],
                ask_them=["How is the team organised?"])


INTERVIEW = {"message_id": "<a@hirevue.example>", "company": "Acme", "title": None, "sender": "Acme <interviews@hirevue.com>",
             "subject": "Interview with Acme", "received_at": at(1), "job_id": None, "mailbox": "alex@gmail.com"}


def mailbox(monkeypatch, body="Please record 3 answers by Friday."):
    """The mailbox read-only: fetch_body answers `body` and counts its calls."""
    calls = []
    monkeypatch.setenv("MAILBOXES", "alex@gmail.com:apppassword")
    monkeypatch.setattr(applications, "fetch_body", lambda user, password, message_id: calls.append(message_id) or body)
    return calls


def test_interview_prep_is_written_once_per_company_and_title(monkeypatch, tmp_path):
    claude = FakeClaude(prep())
    fetched = mailbox(monkeypatch)
    switched_on(monkeypatch, tmp_path, claude)  # switch missing: off
    assert applications.interview_prep(FakeConn(), [INTERVIEW]) == (0, 0) and claude.calls == 0 and fetched == []
    switched_on(monkeypatch, tmp_path, claude, interview_prep=True)
    assert applications.interview_prep(FakeConn(), [INTERVIEW])[0] == 1 and claude.calls == 1
    file = tmp_path / "output" / "interviews" / "acme" / "notes.md"
    notes = file.read_text(encoding="utf-8")
    assert notes.count("**Q") == 8 and "## Your three strongest stories" in notes and "## Questions to ask them" in notes
    assert applications.interview_prep(FakeConn(), [{**INTERVIEW, "subject": "Interview with Acme, again"}])[0] == 0
    assert claude.calls == 1 and fetched == ["<a@hirevue.example>"] and file.read_text(encoding="utf-8") == notes  # no second call, no second download


def test_interview_prep_uses_only_the_email_text_and_says_so_when_there_is_none(monkeypatch, tmp_path):
    claude = FakeClaude(prep())
    switched_on(monkeypatch, tmp_path, claude, interview_prep=True)
    mailbox(monkeypatch, "Please record 3 answers by Friday.")
    applications.interview_prep(FakeConn(), [INTERVIEW])
    asked = claude.kwargs["messages"][0]["content"]
    assert "Please record 3 answers by Friday." in asked and "subject Interview with Acme" in asked
    assert "No detail given in the email" in claude.kwargs["system"][0]["text"] and "Use only <source> and <cv>" in claude.kwargs["system"][0]["text"]


def test_interview_prep_thrown_away_is_tried_again_tomorrow_not_every_hour(monkeypatch, tmp_path):
    folder = tmp_path / "output" / "interviews" / "acme"
    file = folder / "notes.md"
    mailbox(monkeypatch, None)  # the email is not in All Mail: nothing to base notes on, no call, no marker
    claude = FakeClaude(prep())
    switched_on(monkeypatch, tmp_path, claude, interview_prep=True)
    assert applications.interview_prep(FakeConn(), [INTERVIEW], TODAY) == (0, 1) and claude.calls == 0 and not file.exists()
    assert not folder.exists()
    fetched = mailbox(monkeypatch)
    bad = FakeClaude(prep(questions=7))  # thrown away by the checks: no notes, a marker for the day
    switched_on(monkeypatch, tmp_path, bad, interview_prep=True)
    assert applications.interview_prep(FakeConn(), [INTERVIEW], TODAY)[:2] == (0, 1) and bad.calls == 1 and not file.exists()
    assert (folder / ".tried-2026-10-20").exists()
    for _ in range(3):  # the hourly runs of the same Cairo day: not asked, not downloaded
        assert applications.interview_prep(FakeConn(), [INTERVIEW], TODAY)[:2] == (0, 0)
    assert bad.calls == 1 and len(fetched) == 1
    worse = FakeClaude(prep(stories=2))  # tomorrow it is asked again
    switched_on(monkeypatch, tmp_path, worse, interview_prep=True)
    assert applications.interview_prep(FakeConn(), [INTERVIEW], TODAY + timedelta(days=1))[:2] == (0, 1) and worse.calls == 1
    good = FakeClaude(prep())  # and a good answer is written, marker or not
    switched_on(monkeypatch, tmp_path, good, interview_prep=True)
    assert applications.interview_prep(FakeConn(), [INTERVIEW], TODAY + timedelta(days=2))[:2] == (1, 1) and file.exists()
    assert applications.interview_prep(FakeConn(), [INTERVIEW], TODAY + timedelta(days=3))[:2] == (0, 0)  # written: never again


def test_interview_prep_is_checked_on_claudes_text_not_on_the_emails_subject(monkeypatch, tmp_path):
    mailbox(monkeypatch)
    slack = {**INTERVIEW, "company": "Slack", "subject": "Interview with Slack: closing the gap in our team"}  # the email's words
    claude = FakeClaude(prep())
    switched_on(monkeypatch, tmp_path, claude, interview_prep=True)
    assert applications.interview_prep(FakeConn(), [slack], TODAY)[:2] == (1, 1)
    assert "closing the gap in our team" in (tmp_path / "output" / "interviews" / "slack" / "notes.md").read_text(encoding="utf-8")
    other = {**INTERVIEW, "company": "Globex"}  # but a gap in what Claude wrote is thrown away
    gap = prep()
    gap.ask_them.append("How would you close my skills gap in Airflow?")
    switched_on(monkeypatch, tmp_path, FakeClaude(gap), interview_prep=True)
    assert applications.interview_prep(FakeConn(), [other], TODAY)[:2] == (0, 1)
    assert not (tmp_path / "output" / "interviews" / "globex" / "notes.md").exists()


def test_funnel_counts_fall_down_the_stages_and_group_by_company():
    li = "LinkedIn <jobs-noreply@linkedin.com>"
    rows = [("Acme", None, "applied", at(30), li), ("Acme", None, "viewed", at(25), li), ("Acme", None, "interview", at(20), "x"),
            ("Acme", None, "applied", at(5), li),  # a second confirmation: still one application
            ("Globex", None, "interview", at(9), "x"), ("Globex", None, "applied", at(12), "Workable <a@workablemail.com>"),
            ("Initech", None, "applied", at(4), ME), ("Initech", None, "offer", at(1), "x"),
            ("Hooli", None, "viewed", at(3), li),  # a view alone: not an application
            ("Soylent", None, "applied", at(2), "hr@soylent.example"), ("Soylent", None, "rejected", at(1), "x"),  # rejected is no stage
            # a job that came through an introduction: no confirmation email, an interview then an offer
            ("Zyntra", None, "interview", at(50), "jo@gmail.example"), ("Zyntra", None, "offer", at(40), "jo@zyntra.example"),
            ("Zyntra", None, "hired", at(38), "sign@esign.example")]  # the offer signed
    apps = application_stages(rows, ME)
    assert funnel_counts(apps) == {"applied": 5, "viewed": 4, "interview": 4, "offer": 2, "hired": 1}  # a later stage implies the earlier
    assert sorted((a["month"], a["channel"]) for a in apps) == [("2026-08", "Direct contact"), ("2026-09", "LinkedIn"),
                                                               ("2026-10", "Company site"), ("2026-10", "Direct email"),
                                                               ("2026-10", "Workable")]
    assert funnel_counts([]) == {"applied": 0, "viewed": 0, "interview": 0, "offer": 0, "hired": 0}


@pytest.mark.parametrize("sender, expected", [
    ("LinkedIn <jobs-noreply@linkedin.com>", "LinkedIn"), ("Workable <noreply@candidates.workablemail.com>", "Workable"),
    ('"Acme @ icims" <acme+autoreply@talent.icims.com>', "iCIMS"), ("umbrella@myworkday.com", "Workday"),
    (f"Alex <{ME}>", "Direct email"), ("hr.recruitment@acme.example", "Company site"), (None, "Company site")])
def test_channel_from_the_sender(sender, expected):
    assert channel(sender, ME) == expected


def test_a_follow_up_counts_as_answered_only_by_news_after_its_day(monkeypatch, tmp_path):
    """The drafts are found by their file name (slug of the first confirmation's company and title), so a draft you edited
    still counts; news on the follow-up's own Cairo day or before it is not an answer; the 14th day's draft is the same
    application as the 7th's; an email naming no company answers by its title."""
    day7 = at(7).astimezone(applications.CAIRO).date() + timedelta(days=7)  # TODAY
    rows = [("Zz Acme, Ltd.", "Data Engineer", "applied", at(14)), ("ZZ ACME LTD", None, "viewed", at(-1)),    # viewed after
            ("Zz Globex", "BI Developer", "applied", at(7)), (None, "BI Developer", "rejected", at(-2)),       # by its title
            ("Zz Initech", "Data Analyst", "applied", at(14)), ("Zz Initech", None, "rejected", at(0)),         # on the day: no
            ("Zz Umbrella", "Data Engineer", "applied", at(7)),                                                 # no draft: left out
            ("Zz Hooli", "AI Engineer", "applied", at(14))]                                                     # nothing since
    for day, name in ((day7 - timedelta(days=7), "zz-acme-ltd-data-engineer"), (day7, "zz-acme-ltd-data-engineer"),
                      (day7, "zz-globex-bi-developer"), (day7, "zz-initech-data-analyst"), (day7, "zz-hooli-ai-engineer")):
        folder = tmp_path / "followups" / str(day)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.md").write_text("Subject: changed by you\n\nno footer", encoding="utf-8")
    monkeypatch.setattr(applications, "OUTPUT", tmp_path)

    class Conn:
        def execute(self, sql, params=()):
            assert "core.application_email" in sql
            return type("R", (), {"fetchall": lambda _: rows})()

    replies = {(r["company"], r["answer"], r["followed_up"]) for r in applications.follow_up_answers(Conn())}
    assert replies == {("Zz Acme, Ltd.", "viewed", day7 - timedelta(days=7)), ("Zz Globex", "rejected", day7),
                       ("Zz Initech", None, day7), ("Zz Hooli", None, day7)}


# ---- against the warehouse: every row is written in a transaction that is rolled back; skipped without it ----
from pathlib import Path  # noqa: E402

import psycopg  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from job_radar import db, steps  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


@pytest.fixture
def conn():
    try:
        connection = db.connect()
    except (KeyError, psycopg.OperationalError) as e:
        pytest.skip(f"no warehouse: {e!r}"[:120])
    yield connection
    connection.rollback()  # nothing the test wrote stays
    connection.close()


def add_job(conn, key: str) -> int:
    return conn.execute(
        "INSERT INTO core.job (job_key, title, company, location, place, source, job_url, role_rank, role, target_company,"
        " first_seen) VALUES (%s, 'Data Engineer', 'Acme', 'Cairo', 'Egypt', 'test', %s, 1, 'Data Engineer', false, current_date)"
        " RETURNING job_id", (f"test_applications {key}", f"https://test.example/{key}")).fetchone()[0]


@pytest.mark.parametrize("now, incoming, status, moved", [
    (None, "applied", "applied", 1),            # no row yet: created
    ("new", "saved", "saved", 1), ("saved", "applied", "applied", 1), ("applied", "interview", "interview", 1),
    ("interview", "rejected", "rejected", 1), ("rejected", "offer", "offer", 1),
    ("interview", "applied", "interview", 0),   # never down
    ("offer", "rejected", "offer", 0), ("offer", "applied", "offer", 0), ("rejected", "interview", "rejected", 0),
    ("applied", "applied", "applied", 0),       # the same status again changes nothing
    ("ignored", "interview", "ignored", 0)])    # 'ignored' is yours: it is not on the ladder and never moves
def test_a_status_moves_up_the_ladder_only(conn, now, incoming, status, moved):
    job_id = add_job(conn, f"{now} {incoming}")
    if now:
        conn.execute("INSERT INTO core.application (job_id, status, note) VALUES (%s, %s, 'first note')", (job_id, now))
    with conn.cursor() as cur:
        assert applications.move_status(cur, job_id, incoming, "from your mailbox 2026-10-20") == moved
    note = "from your mailbox 2026-10-20"
    expected = note if now is None else f"first note; {note}" if moved else "first note"
    assert conn.execute("SELECT status, note FROM core.application WHERE job_id = %s", (job_id,)).fetchone() == (status, expected)


def test_the_ladder_runs_new_to_offer_and_ignored_is_not_on_it():
    assert applications.LADDER == ["new", "saved", "applied", "interview", "rejected", "offer"]


def test_the_drafts_and_the_tab_list_the_same_follow_ups(conn):
    from tracker.applications_tab import follow_ups

    today = applications.cairo_today()
    for n, (company, days, hour, minute) in enumerate([("Zz Seven", 7, 23, 59), ("Zz Six", 6, 0, 1), ("Zz Eight", 8, 12, 0),
                                                       ("Zz Fourteen", 14, 0, 1), ("Zz Thirteen", 13, 23, 59)]):
        received = datetime.combine(today - timedelta(days=days), time(hour, minute), applications.CAIRO)
        conn.execute("INSERT INTO core.application_email (message_id, received_at, sender, subject, company, kind)"
                     " VALUES (%s, %s, 'hr@zz.example', 'Thank you for applying', %s, 'applied')", (f"<zz-days-{n}>", received, company))
    mine = lambda rows: sorted(r[0] for r in rows if r[0].startswith("Zz "))  # noqa: E731
    drafted = mine(applications.due_now(conn))
    listed = follow_ups(conn)
    assert drafted == ["Zz Fourteen", "Zz Seven"]  # exactly 7 and 14 Cairo days, 23:59 or 00:01 alike
    assert mine(listed.itertuples(index=False)) == drafted  # the tab shows the rule of the email and the drafts, not its own
    assert listed.set_index("company").loc[["Zz Seven", "Zz Fourteen"], "days"].tolist() == [7, 14]


def test_the_email_lists_the_follow_ups_the_drafts_are_written_for(conn, monkeypatch):
    """steps.email takes its follow-ups from applications.due_follow_ups, so a company is the same company in the email and in
    the drafts: whatever its punctuation and case, and a rejection that names no company is matched by its title."""
    import smtplib

    today = applications.cairo_today()
    for n, (company, title, kind, days) in enumerate([
            ("Zz Acme, Ltd.", "Data Engineer", "applied", 7), ("ZZ ACME Ltd", None, "rejected", 2),   # answered under another spelling
            ("Zz Globex", "BI Developer", "applied", 7), (None, "BI Developer", "rejected", 2),       # answered, naming only the title
            ("Zz Initech", "Data Analyst", "applied", 14), ("zz initech", "data analyst", "applied", 8),  # one application, 14 days old
            ("Zz Umbrella", "Data Engineer", "applied", 7)]):                                          # nothing since: listed
        conn.execute("INSERT INTO core.application_email (message_id, received_at, sender, subject, company, title, kind)"
                     " VALUES (%s, %s, 'hr@zz.example', 'Thank you', %s, %s, %s)",
                     (f"<zz-email-{n}>", datetime.combine(today - timedelta(days=days), time(10), applications.CAIRO), company, title, kind))
    add_job(conn, "email follow-ups")  # the email is written only when a job is new
    sent = []
    monkeypatch.setattr(steps, "digest", lambda jobs, name, follow_ups=(), problems=(): sent.append(list(follow_ups)) or "body")
    monkeypatch.setattr(steps, "send", lambda body, title, name: False)  # nothing is sent, so nothing is marked emailed
    monkeypatch.setattr(smtplib, "SMTP_SSL", lambda *a, **k: pytest.fail("an email was sent"))
    steps.email(conn, home=True)
    mine = [(f["company"], f["title"], f["applied_at"]) for f in sent[0] if f["company"].lower().startswith("zz ")]
    assert mine == [("Zz Initech", "Data Analyst", today - timedelta(days=14)), ("Zz Umbrella", "Data Engineer", today - timedelta(days=7))]

import imaplib  # noqa: E402


# ---- the mailboxes: MAILBOXES in .env, each read for its own window (fictional addresses) ----

def test_accounts_are_the_gmail_entries_of_mailboxes_else_gmail_user(monkeypatch, capsys):
    monkeypatch.setenv("MAILBOXES", "Alex@gmail.com:abcd efgh,sam@googlemail.com:ijklmnop:imap.gmail.com,lee@yahoo.com:qrstuvwx,"
                                    "nopass@gmail.com,  ")
    monkeypatch.setenv("GMAIL_USER", "fallback@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "wxyz wxyz")
    assert applications.accounts() == {"alex@gmail.com": "abcdefgh", "sam@googlemail.com": "ijklmnop"}  # Gmail only, in order
    assert "2 MAILBOXES entries are not Gmail accounts with a password, left out" in capsys.readouterr().out
    for none in ("", "lee@yahoo.com:qrstuvwx"):  # nothing usable in MAILBOXES: GMAIL_USER
        monkeypatch.setenv("MAILBOXES", none)
        assert applications.accounts() == {"fallback@gmail.com": "wxyzwxyz"}
    monkeypatch.delenv("GMAIL_USER")
    assert applications.accounts() == {}


class NoCommit:
    """The warehouse connection of the fixture with commit turned into nothing: the fixture rolls everything back."""

    def __init__(self, conn):
        self.conn = conn

    def __getattr__(self, name):
        return getattr(self.conn, name)

    def commit(self):
        pass


def mail(message_id, kind="applied", company="Zz Acme"):
    return {"message_id": message_id, "received_at": at(1), "sender": "hr@zz.example", "subject": "Thank you for applying",
            "to": "", "kind": kind, "company": company, "title": None}


def read_from(monkeypatch, mailboxes: dict, down=()):
    """track_applications with read_mailbox replaced: answers `mailboxes` {address: [mail, ...]} the way the real one does (an
    email already stored is skipped), raises for the addresses in `down`; returns the (address, days) of every read."""
    reads = []

    def fake(user, password, days, stored):
        reads.append((user, days))
        if user in down:
            raise imaplib.IMAP4.error("down")
        return [m for m in mailboxes[user] if m["message_id"] not in stored], [], len(mailboxes[user])

    monkeypatch.setattr(applications, "read_mailbox", fake)
    monkeypatch.setitem(applications.SETTINGS, "features", {"track_applications": True})
    monkeypatch.setenv("MAILBOXES", ",".join(f"{address}:pass{n}" for n, address in enumerate(mailboxes)))
    return reads


def test_each_mailbox_is_read_for_its_own_window_and_stored_with_its_mailbox(conn, monkeypatch):
    conn.execute("INSERT INTO core.application_email (message_id, received_at, sender, subject, kind, mailbox)"
                 " VALUES ('<zz-old>', %s, 'hr@zz.example', 'Thank you', 'applied', 'zz-a@gmail.com')", (at(30),))
    reads = read_from(monkeypatch, {"zz-a@gmail.com": [mail("<zz-a-1>")],
                                    "zz-b@gmail.com": [mail("<zz-shared>"), mail("<zz-b-1>", "interview")],
                                    "zz-c@gmail.com": [mail("<zz-shared>")]})  # the same email in two mailboxes
    applications.track_applications(NoCommit(conn))
    assert reads == [("zz-a@gmail.com", 14), ("zz-b@gmail.com", 400), ("zz-c@gmail.com", 400)]  # no rows yet: 400 days; a's newest is 30 days old: the 14-day gap cap
    assert conn.execute("SELECT message_id, mailbox FROM core.application_email WHERE message_id LIKE '<zz-%' ORDER BY 1").fetchall() == [
        ("<zz-a-1>", "zz-a@gmail.com"), ("<zz-b-1>", "zz-b@gmail.com"), ("<zz-old>", "zz-a@gmail.com"),
        ("<zz-shared>", "zz-b@gmail.com")]  # stored once, with the first mailbox that had it
    reads.clear()
    applications.track_applications(NoCommit(conn))  # rerun: every mailbox with rows now reads 3 days, and nothing is added
    gap = {box: min(14, max(3, (datetime.now(timezone.utc) - newest).days + 2)) for box, newest in conn.execute(
        "SELECT mailbox, max(received_at) FROM core.application_email WHERE message_id LIKE '<zz-%' GROUP BY 1")}
    assert reads == [("zz-a@gmail.com", gap["zz-a@gmail.com"]), ("zz-b@gmail.com", gap["zz-b@gmail.com"]),
                     ("zz-c@gmail.com", 400)]  # from each mailbox's newest stored email; c holds no row of its own
    assert conn.execute("SELECT count(*) FROM core.application_email WHERE message_id LIKE '<zz-%'").fetchone()[0] == 4


def test_one_mailbox_down_does_not_stop_the_others_and_all_down_fails(conn, monkeypatch, capsys):
    read_from(monkeypatch, {"zz-a@gmail.com": [mail("<zz-a-2>")], "zz-b@gmail.com": [mail("<zz-b-2>")]}, down={"zz-a@gmail.com"})
    applications.track_applications(NoCommit(conn))
    out = capsys.readouterr().out
    assert "WARNING track_applications: mailbox 1 of 2:" in out and "zz-a" not in out  # never the address
    assert "1 of 2 mailboxes read" in out
    assert conn.execute("SELECT mailbox FROM core.application_email WHERE message_id LIKE '<zz-%-2>'").fetchall() == [("zz-b@gmail.com",)]
    read_from(monkeypatch, {"zz-a@gmail.com": [], "zz-b@gmail.com": []}, down={"zz-a@gmail.com", "zz-b@gmail.com"})
    with pytest.raises(RuntimeError, match="no mailbox could be read"):
        applications.track_applications(NoCommit(conn))
    read_from(monkeypatch, {"zz-new@gmail.com": []})  # a mailbox with nothing stored and nothing found: the wrong mailbox
    with pytest.raises(RuntimeError, match="no mailbox could be read"):
        applications.track_applications(NoCommit(conn))
    assert "no email in the last 400 days: wrong mailbox or folder?" in capsys.readouterr().out


def test_interview_prep_reads_the_email_from_its_own_mailbox(monkeypatch, tmp_path):
    monkeypatch.setenv("MAILBOXES", "one@gmail.com:aaaa,two@gmail.com:bbbb")
    asked = []
    monkeypatch.setattr(applications, "fetch_body", lambda user, password, message_id: asked.append((user, password, message_id)) or "Please call us.")
    claude = FakeClaude(prep())
    switched_on(monkeypatch, tmp_path, claude, interview_prep=True)
    second = {**INTERVIEW, "message_id": "<b@hirevue.example>", "company": "Globex", "mailbox": "two@gmail.com"}
    gone = {**INTERVIEW, "message_id": "<c@hirevue.example>", "company": "Initech", "mailbox": "removed@gmail.com"}
    assert applications.interview_prep(FakeConn(), [{**INTERVIEW, "mailbox": "one@gmail.com"}, second, gone], TODAY)[:2] == (2, 3)
    assert asked == [("one@gmail.com", "aaaa", "<a@hirevue.example>"), ("two@gmail.com", "bbbb", "<b@hirevue.example>")]  # its own password
    assert claude.calls == 2  # the mailbox no longer in MAILBOXES is skipped, not guessed


def test_the_search_takes_offers_and_every_email_from_the_application_senders(monkeypatch):
    monkeypatch.delenv("APPLICATION_SENDERS", raising=False)
    assert applications.search() == applications.SEARCH and "subject:(offer letter)" in applications.SEARCH
    assert "OR offer)" not in applications.SEARCH  # "offer" alone brought food deals
    monkeypatch.setenv("APPLICATION_SENDERS", " zyntra-fold.example , Jo@ZZ.example = Zyntra Fold, kim@zz.example,")
    assert applications.senders() == {"zyntra-fold.example": "Zyntra Fold", "jo@zz.example": "Zyntra Fold", "kim@zz.example": None}
    assert applications.search() == f"({applications.SEARCH} OR from:(zyntra-fold.example OR jo@zz.example OR kim@zz.example))"


@pytest.mark.parametrize("sender, company", [
    ("Jo Doe <jo@zz.example>", "Zyntra Fold"),               # a listed address, with its company
    ("Ann <ann@zyntra-fold.example>", "Zyntra Fold"),        # a listed domain names its own
    ("noreply@mail.zyntra-fold.example", "Zyntra Fold"),     # a subdomain of it
    ("x@notzyntra-fold.example", None), ("kim@zz.example", None), ("hr@other.example", None)])
def test_an_email_that_names_no_company_takes_its_listed_senders(monkeypatch, sender, company):
    monkeypatch.setenv("APPLICATION_SENDERS", "zyntra-fold.example,jo@zz.example=Zyntra Fold,kim@zz.example")
    assert applications.sender_company(sender) == company


def test_no_interview_prep_for_a_company_that_already_sent_an_offer(conn, monkeypatch):
    read_from(monkeypatch, {"zz-a@gmail.com": [mail("<zz-int-1>", "interview", "Zyntrafold Labs"), mail("<zz-off-1>", "offer", "ZYNTRAFOLD LABS"),
                                               mail("<zz-int-2>", "interview", "Zz Quorvex")]})
    prepped = []
    monkeypatch.setattr(applications, "interview_prep", lambda conn, mails: prepped.extend(m["message_id"] for m in mails) or (0, 0))
    applications.track_applications(NoCommit(conn))
    assert "<zz-int-2>" in prepped and "<zz-int-1>" not in prepped  # its interview is behind you


def test_a_stored_email_with_no_company_gets_its_senders(conn, monkeypatch):
    monkeypatch.setenv("APPLICATION_SENDERS", "jo@zz.example=Zyntra Fold")
    intro = {**mail("<zz-intro>", "interview", None), "sender": "Jo <jo@zz.example>"}
    named = {**mail("<zz-named>", "offer", "Zz Quorvex"), "sender": "Jo <jo@zz.example>"}  # Claude's company is kept
    read_from(monkeypatch, {"zz-a@gmail.com": [intro, named]})
    applications.track_applications(NoCommit(conn))
    assert conn.execute("SELECT message_id, company FROM core.application_email WHERE message_id IN ('<zz-intro>', '<zz-named>')"
                        " ORDER BY 1").fetchall() == [("<zz-intro>", "Zyntra Fold"), ("<zz-named>", "Zz Quorvex")]


def test_the_tab_counts_companies_not_emails(conn):
    """An offer thread of many emails is one offer; "ZZ QUORVEX" and "Zz Quorvex" are one company; an email naming no
    company counts by its title, and one naming neither is not counted."""
    from tracker.applications_tab import COMPANIES_BY_KIND
    before = dict(conn.execute(COMPANIES_BY_KIND).fetchall())
    for n, (kind, company, title) in enumerate([("offer", "Zz Quorvex", None), ("offer", "ZZ QUORVEX", None),
                                                ("offer", "Zz Quorvex", "Data Engineer"), ("interview", "Zz Quorvex", None),
                                                ("interview", None, "Zz Analyst"), ("interview", None, None)]):
        conn.execute("INSERT INTO core.application_email (message_id, received_at, sender, subject, company, title, kind)"
                     " VALUES (%s, %s, 'hr@zz.example', 's', %s, %s, %s)", (f"<zz-count-{n}>", at(1), company, title, kind))
    after = dict(conn.execute(COMPANIES_BY_KIND).fetchall())
    assert after["offer"] - before.get("offer", 0) == 1
    assert after["interview"] - before.get("interview", 0) == 2


def test_a_company_that_never_answered_in_30_days_has_ghosted_you(conn):
    from tracker.applications_tab import APPLICATIONS, COMPANIES_BY_KIND, GHOSTED_DAYS
    before = dict(conn.execute(COMPANIES_BY_KIND).fetchall())["ghosted"]
    for n, (company, kind, days) in enumerate([("Zz Ghostly", "applied", GHOSTED_DAYS + 5), ("Zz Ghostly", "viewed", 3),  # a view is no answer
                                               ("Zz Answered", "applied", GHOSTED_DAYS + 5), ("Zz Answered", "rejected", 2),
                                               ("Zz Recent", "applied", GHOSTED_DAYS - 5)]):
        conn.execute("INSERT INTO core.application_email (message_id, received_at, sender, subject, company, kind)"
                     " VALUES (%s, now() - make_interval(days => %s), 'hr@zz.example', 's', %s, %s)",  # ghosted counts from now
                     (f"<zz-ghost-{n}>", days, company, kind))
    assert dict(conn.execute(COMPANIES_BY_KIND).fetchall())["ghosted"] - before == 1
    news = dict(conn.execute(f"SELECT company, latest_news FROM ({APPLICATIONS}) a WHERE company LIKE 'Zz %%'").fetchall())
    assert news == {"Zz Ghostly": "ghosted", "Zz Answered": "rejected", "Zz Recent": "applied"}


def test_a_hired_email_moves_its_jobs_status_to_offer_the_top_of_the_ladder(conn):
    job = conn.execute("INSERT INTO core.job (job_key, title, company, location, place, source, job_url, role_rank, role,"
                       " target_company, first_seen) VALUES ('zz hired | zz quorvex', 'Data Engineer', 'Zz Quorvex', 'Cairo',"
                       " 'Egypt', 'test', 'https://test.example/zz-hired', 1, 'Data Engineer', false, current_date - 60)"
                       " RETURNING job_id").fetchone()[0]
    jobs = [(job, "zz hired | zz quorvex", "Zz Quorvex", date(2026, 8, 1))]
    with conn.cursor() as cur:
        applications.store(cur, jobs, "zz-a@gmail.com", [mail("<zz-hired>", "hired", "Zz Quorvex")])
    assert conn.execute("SELECT status FROM core.application WHERE job_id = %s", (job,)).fetchone() == ("offer",)
    assert conn.execute("SELECT kind FROM core.application_email WHERE message_id = '<zz-hired>'").fetchone() == ("hired",)


def test_a_company_that_hired_you_shows_hired_and_your_start_date(conn):
    """A signature reminder after the signed offer does not undo hired; the start date (core.hire) matches the company by
    its letters alone."""
    from tracker.applications_tab import APPLICATIONS
    for n, (kind, days) in enumerate([("interview", 40), ("offer", 30), ("hired", 28), ("offer", 20)]):
        conn.execute("INSERT INTO core.application_email (message_id, received_at, sender, subject, company, kind)"
                     " VALUES (%s, now() - make_interval(days => %s), 'hr@zz.example', 's', 'ZZ QUORVEX', %s)",
                     (f"<zz-hire-{n}>", days, kind))
    conn.execute("INSERT INTO core.hire (company, started_on) VALUES ('Zz Quorvex', '2026-09-01')")
    row = conn.execute(f"SELECT latest_news, started_on FROM ({APPLICATIONS}) a WHERE company = 'ZZ QUORVEX'").fetchone()
    assert row == ("hired", date(2026, 9, 1))


def test_a_rejection_naming_another_company_is_matched_by_its_sender_and_html_is_stripped(conn):
    """Raya's rejection came from the address of its confirmation but named its holding company, with HTML tags in the
    subject: rematch gives it the confirmation's job (the only one from that address) and moves the job to rejected."""
    assert applications.clean("Update with <b><span>AMAN Holding</span></b>") == "Update with AMAN Holding"
    assert applications.clean("Raya Careers <hr@zzraya.example>") == "Raya Careers <hr@zzraya.example>"
    job_id = conn.execute(
        "INSERT INTO core.job (job_key, title, company, location, place, source, job_url, role_rank, role, target_company,"
        " first_seen, in_scope) VALUES ('zz riser | zzraya', 'Riser - AI Engineer', 'ZzRaya', 'Cairo', 'Egypt', 'manual',"
        " 'https://test.example/zzraya', 1, 'AI Engineer', false, current_date - 20, true) RETURNING job_id").fetchone()[0]
    conn.execute("INSERT INTO core.application_email (message_id, received_at, sender, subject, company, kind, job_id)"
                 " VALUES ('<zzr-1>', %s, 'Raya Careers <hr@zzraya.example>', 'Received', 'ZzRaya', 'applied', %s),"
                 " ('<zzr-2>', %s, 'Raya Careers <hr@zzraya.example>', 'Update', 'AMAN Holding', 'rejected', NULL)",
                 (at(15), job_id, at(14)))
    with conn.cursor() as cur:
        matched, moved = applications.rematch(cur, [])
    assert conn.execute("SELECT job_id FROM core.application_email WHERE message_id = '<zzr-2>'").fetchone()[0] == job_id
    assert conn.execute("SELECT status FROM core.application WHERE job_id = %s", (job_id,)).fetchone()[0] == "rejected"


def test_a_rejection_matched_to_the_job_ends_its_follow_up_whatever_company_it_names(conn):
    """due_now reads a matched email's company from its job: Raya's rejection named AMAN Holding but answers Raya."""
    job_id = conn.execute(
        "INSERT INTO core.job (job_key, title, company, location, place, source, job_url, role_rank, role, target_company,"
        " first_seen, in_scope) VALUES ('zz fu | zzrayafu', 'Riser', 'ZzRayaFu', 'Cairo', 'Egypt', 'manual',"
        " 'https://test.example/zzrayafu', 1, 'AI Engineer', false, current_date - 20, true) RETURNING job_id").fetchone()[0]
    applied = datetime.now(timezone.utc) - timedelta(days=14)
    conn.execute("INSERT INTO core.application_email (message_id, received_at, sender, subject, company, kind, job_id)"
                 " VALUES ('<zzfu-1>', %s, 'hr@zzrayafu.example', 'Received', 'ZzRayaFu', 'applied', %s)", (applied, job_id))
    today = applied.astimezone(applications.CAIRO).date() + timedelta(days=14)
    assert [d[0] for d in applications.due_now(conn, today)].count("ZzRayaFu") == 1
    conn.execute("INSERT INTO core.application_email (message_id, received_at, sender, subject, company, kind, job_id)"
                 " VALUES ('<zzfu-2>', %s, 'hr@zzrayafu.example', 'Update', 'ZzAman Holding', 'rejected', %s)",
                 (applied + timedelta(days=1), job_id))
    assert "ZzRayaFu" not in [d[0] for d in applications.due_now(conn, today)]
