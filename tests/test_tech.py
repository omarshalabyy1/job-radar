"""tech.is_tech_role: which titles the wide collection for the website keeps."""

import pytest

from job_radar.tech import is_tech_role


@pytest.mark.parametrize("title", [
    "Warehouse Keeper (Experience in the pharmaceutical field is required)",  # tanqeeb, 2026-10-18
    "Warehouse Coordinator",
    "Senior UX Copywriter",
])
def test_non_tech_titles_are_dropped(title):
    assert not is_tech_role(title)


@pytest.mark.parametrize("title", [
    "Data Warehouse Lead",
    "DataWarehouse Engineer",
    "DWH Developer",
    "Warehouse Management System Developer",
    "Senior UX Designer",
])
def test_tech_titles_are_kept(title):
    assert is_tech_role(title)
