"""scripts/audit_apps.py: what counts as a source in use."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import audit_apps  # noqa: E402


def test_group_of_puts_each_source_in_its_group():
    assert audit_apps.group_of("wuzzuf") == "Egypt boards" and audit_apps.group_of("bayt") == "Gulf boards"
    assert audit_apps.group_of("inclusivelyremote") == "Remote boards" and audit_apps.group_of("wellfound") == "Startup boards"
    assert audit_apps.group_of("apify:linkedin_gulf") == "LinkedIn and Apify" and audit_apps.group_of("linkedin") == "LinkedIn and Apify"
    assert audit_apps.group_of("email") == "Email alerts" and audit_apps.group_of("manual:wuzzuf-keywords") == "Manual scrapes"
    assert audit_apps.group_of("ashby-board") == "Company career pages" and audit_apps.group_of("canonical") == "Company career pages"


def test_verdict_names_what_to_look_at():
    assert audit_apps.verdict(postings=30, yours=4) == "working"
    assert audit_apps.verdict(postings=30, yours=0) == "feeds the website only"
    assert audit_apps.verdict(postings=0, yours=0) == "NO POSTINGS in 7 days"
