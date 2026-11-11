"""A Wellfound job says "remote": true and has no location: it was stored as "Unknown location", out of reach, so the board gave 0 jobs of
yours (audit 2026-11-10). A remote job with no place is a Remote job; one with a place keeps it."""

from job_radar.sources import apify


def test_a_remote_item_with_no_location_is_a_remote_posting():
    rows = apify.normalize("wellfound", [{"title": "Senior Engineer, AI", "remote": True, "detailUrl": "https://wellfound.com/jobs/1-x",
                                          "companyName": "Acme"}])
    assert rows[0]["searched_for"] == "Remote"


def test_an_item_with_a_location_or_not_remote_keeps_its_own_place():
    rows = apify.normalize("wellfound", [
        {"title": "Data Engineer", "remote": True, "location": "Cairo, Egypt", "detailUrl": "https://wellfound.com/jobs/2-x"},
        {"title": "Data Analyst", "remote": False, "detailUrl": "https://wellfound.com/jobs/3-x"}])
    assert [r["searched_for"] for r in rows] == [None, None]
