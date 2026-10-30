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


def test_a_neon_outage_is_a_warning_and_the_emails_wait_for_the_next_run(monkeypatch, capsys):
    """Neon down (over its quota, or no network): a WARNING line and a green task, not a red one every hour."""
    import psycopg

    from job_radar import publish_applications as pa

    class Empty:
        def execute(self, *a, **k):
            return self

        def fetchall(self):
            return []

        def __iter__(self):
            return iter([])

    def refused(*a, **k):
        raise psycopg.OperationalError("connection failed: exceeded the quota")

    monkeypatch.setenv("NEON_DATABASE_URL", "postgresql://user:pw@neon.example/db")
    monkeypatch.setattr(pa.psycopg, "connect", refused)
    pa.publish_applications(Empty())
    out = capsys.readouterr().out
    assert out.startswith("WARNING publish_applications: cannot connect to Neon") and "pw@" not in out
