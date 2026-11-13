"""The mailbox search finds assessments, exams, screenings and calendar invitations, and Workable's confirmations (their
subject is only "<role> - <company>"): the 2026-11-13 full-text scan found an IBM coding assessment, a TP interview
invitation, a Valeo HackerRank exam and Workable confirmations the subject words missed."""

from job_radar import applications


def test_the_search_takes_assessments_exams_screenings_and_invitations():
    for word in ("assessment", "exam", "screening", "hackerrank", "invitation", "shortlisted"):
        assert f" OR {word}" in applications.SEARCH


def test_the_search_takes_every_workable_confirmation():
    assert "from:(candidates.workablemail.com)" in applications.SEARCH
