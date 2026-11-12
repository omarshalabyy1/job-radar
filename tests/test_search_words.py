"""A rejection with no word like 'application' in its subject ('Your Registration Status', TELUS, 2026-11-09) was never read."""
from job_radar import applications


def test_the_mail_search_reads_registration_and_status_subjects():
    for word in ("registration", "status"):
        assert word in applications.SEARCH
