"""Data and analytics titles the Data Analyst rule missed (audit of Egypt's dropped postings, 2026-11-14): Omar applied to
four of them by hand while the radar left them out as "no role"."""

import pytest

from job_radar.config import ROLE_LABEL, role_of

YOURS = ["ETIC, Data & Analytics Senior Associate", "Data and Analytics Associate", "Data Systems Analyst",
         "Application Developer-Microsoft Analytics", "Enterprise Data Operations Sr Analyst",
         "SAP Analytics Cloud (SAC) Planning Consultant", "Analytics Consultant", "Marketing Science Analyst",
         "Commercial Strategy & Operations Analyst"]
NOT_YOURS = ["Cloud Security Analyst", "Data Security & DLP Analyst", "Financial Analyst", "Credit Analyst",
             "Quality Analyst (French + German/Italian)", "Real-Time Analyst (RTA)", "Data Center Presales Engineer",
             "Senior Fraud analyst- Issuing Fraud management", "Investment Analyst Intern", "Sales Operations Analyst",
             "Human Resources Information System Analyst", "Pricing Analyst II (BPO experience is a must)"]


@pytest.mark.parametrize("title", YOURS)
def test_a_data_or_analytics_title_is_a_data_analyst_role(title):
    assert ROLE_LABEL[role_of(title)] == "Data Analyst"


@pytest.mark.parametrize("title", NOT_YOURS)
def test_other_analysts_stay_out(title):
    assert role_of(title) is None
