"""publish_applications: which stage each job gets from its emails."""

from datetime import datetime, timezone

from job_radar.publish_applications import stages


def mail(day, kind, job_id=1):
    return (f"m{day}{kind}", datetime(2026, 10, day, tzinfo=timezone.utc), "hr@acme.io", f"{kind} subject", "Acme", "Analyst", kind, job_id)


def test_highest_stage_wins_and_dates_are_the_first_email_of_each_stage():
    rows = [mail(3, "applied"), mail(5, "applied"), mail(9, "interview"), mail(7, "viewed"), mail(12, "rejected")]
    stage, first, subject, _ = stages(rows, {1})[1]
    assert stage == "rejected" and subject == "rejected subject"
    assert first == {"applied": rows[0][1], "interview": rows[2][1], "rejected": rows[4][1]}


def test_hired_is_an_offer_viewed_and_other_move_nothing_and_jobs_off_neon_are_left_out():
    assert stages([mail(3, "hired")], {1})[1][0] == "offer"
    assert stages([mail(3, "viewed"), mail(4, "other")], {1}) == {}
    assert stages([mail(3, "applied", job_id=2), mail(4, "applied", job_id=None)], {1}) == {}
