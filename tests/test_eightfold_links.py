"""Ericsson and Microsoft are Eightfold sites: without the tenant in the link (&domain=...), detect() took them for
SuccessFactors, whose search redirected to a sign-in page, and they logged 0 jobs every night (found 2026-11-14;
Ericsson's API lists 12 Egypt jobs)."""

import pytest

from job_radar.config import SETTINGS
from job_radar.sources import companies

LINKS = {c["name"]: c["link"] for c in SETTINGS["companies"]}


@pytest.mark.parametrize("name, tenant", [("Ericsson", "ericsson.com"), ("Microsoft", "microsoft.com"), ("Vodafone", "vodafone.com")])
def test_an_eightfold_site_names_its_tenant(name, tenant):
    assert f"domain={tenant}" in LINKS[name]
    assert companies.detect(LINKS[name])[0] == "eightfold"
