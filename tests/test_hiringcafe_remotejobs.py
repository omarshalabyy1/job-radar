"""hiring.cafe (its search page carries the results as JSON) and RemoteJobs.org (public JSON API), read from saved real
responses of 2026-11-14 (tests/fixtures/hiringcafe.html, remotejobs.json), cut to a few jobs."""
import json
import re
from datetime import date, timedelta
from pathlib import Path

from job_radar.sources import remote

FIXTURES = Path(__file__).parent / "fixtures"
TODAY, OLD = date.today().isoformat(), (date.today() - timedelta(days=20)).isoformat()


class Reply:
    def __init__(self, text):
        self.text = text

    def json(self):
        return json.loads(self.text)


def hiringcafe_page(change) -> str:
    """The saved page with its hits changed by `change(hits)`: IBM (Giza, hybrid), Thndr (Cairo), PwC (Cairo), Amazon (no role of yours)."""
    page = (FIXTURES / "hiringcafe.html").read_text(encoding="utf-8")
    data = json.loads(re.search(r'__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S).group(1))
    hits = data["props"]["pageProps"]["ssrHits"]
    for h in hits:
        h["v5_processed_job_data"]["estimated_publish_date"] = TODAY + "T08:00:00.000Z"
    change(hits)
    return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + "</script>"


def test_hiringcafe_keeps_your_roles_in_egypt_or_remote_posted_this_week(monkeypatch):
    def change(hits):
        hits[1]["v5_processed_job_data"]["estimated_publish_date"] = OLD + "T08:00:00.000Z"  # Thndr: 20 days old
        hits[2]["v5_processed_job_data"].update(workplace_type="Remote", formatted_workplace_location="Egypt or Nigeria")  # PwC
    asked = []
    monkeypatch.setattr(remote, "KEYWORDS", ["data engineer", "power bi"])
    monkeypatch.setattr(remote, "get", lambda url, **params: asked.append((url, params)) or Reply(hiringcafe_page(change)))
    rows = remote.hiringcafe()
    assert [(r["title"], r["company"], r["location"]) for r in rows] == [
        ("Data Engineer-Data Integration", "IBM", "Giza, Cairo, Egypt"),
        ("ETIC, Celonis BI Engineer - German Speaker", "PwC", "Egypt or Nigeria (remote)")]  # each once, though both keywords found it
    r = rows[0]
    assert (r["source"], r["searched_for"], r["date_posted"]) == ("hiringcafe", None, TODAY)
    assert r["job_url"].startswith("https://careers.ibm.com/")  # the employer's own posting
    assert "viewedByUsers" not in json.dumps(r["payload"])  # the site's lists of other users' ids are not kept
    assert not r["description"] and r["payload"]["requirements_summary"]  # a summary is no description: describe reads the page
    state = json.loads(asked[0][1]["searchState"])
    assert asked[0][0] == "https://hiringcafe.com/classic" and state["searchQuery"] == "data engineer" and state["sortBy"] == "date"
    assert state["locations"][0]["address_components"][0]["short_name"] == "EG" and len(asked) == 2


def test_hiringcafe_with_no_results_at_all_says_the_site_changed(monkeypatch, capsys):
    monkeypatch.setattr(remote, "KEYWORDS", ["data engineer"])
    monkeypatch.setattr(remote, "get", lambda url, **params: Reply("<html><body>new design</body></html>"))
    assert remote.hiringcafe() == []
    assert "hiringcafe" in capsys.readouterr().out


def test_remotejobs_keeps_your_roles_from_a_place_in_scope_posted_this_week(monkeypatch):
    saved = json.loads((FIXTURES / "remotejobs.json").read_text(encoding="utf-8"))
    jobs = saved["data"]  # Analytics Engineer III (Brazil), Platform Engineer - AI (India), Enterprise Sales Engineer (US)
    for j in jobs:
        j["posted_at"] = TODAY + "T08:00:00+00:00"
    jobs[0]["location"] = "Worldwide"  # the same job, open to anyone: yours
    asked = []
    monkeypatch.setattr(remote, "KEYWORDS", ["data engineer", "analytics engineer"])
    monkeypatch.setattr(remote, "get", lambda url, **params: asked.append((url, params)) or Reply(json.dumps(saved)))
    rows = remote.remotejobs()
    assert [(r["title"], r["company"], r["location"]) for r in rows] == [
        ("Analytics Engineer III (Data Directorate - FP&A)", jobs[0]["company"]["name"], "Worldwide (remote)")]  # India: out of scope
    assert rows[0]["source"] == "remotejobs" and rows[0]["job_url"].startswith("https://remotejobs.org/remote-jobs/")
    assert asked == [("https://remotejobs.org/api/v1/jobs", {"q": "data engineer", "limit": 50}),
                     ("https://remotejobs.org/api/v1/jobs", {"q": "analytics engineer", "limit": 50})]


def test_jobgether_asks_for_jobs_open_to_egypt_newest_first_and_keeps_your_roles_of_this_week(monkeypatch):
    saved = json.loads((FIXTURES / "jobgether.json").read_text(encoding="utf-8"))
    jobs = saved["jobs"]  # Senior Data Engineer (Anywhere), Data Engineer at Soum (a list with Egypt), Data & Platform Engineer
    jobs[0]["postedAt"], jobs[1]["postedAt"], jobs[2]["postedAt"] = TODAY + "T02:31:33.251Z", OLD + "T02:31:33.251Z", TODAY + "T02:31:33.251Z"
    asked = []
    monkeypatch.setattr(remote, "KEYWORDS", ["data engineer", "power bi"])
    monkeypatch.setattr(remote, "get", lambda url, **params: asked.append((url, params)) or Reply(json.dumps(saved)))
    rows = remote.jobgether()
    assert [(r["title"], r["company"], r["location"], r["date_posted"]) for r in rows] == [
        ("Senior Data Engineer - Datenplattform", "Coopers Group AG", "Anywhere (remote)", TODAY)]  # Soum's is 20 days old
    assert rows[0]["source"] == "jobgether" and rows[0]["job_url"].startswith("https://jobgether.com/offer/")
    assert asked == [("https://jobgether.com/api/v1/jobs", {"keyword": k, "locations": "egypt", "sort": "date", "limit": 25})
                     for k in ("data engineer", "power bi")]


def test_remoterocketship_reads_its_egypt_page_of_each_title_and_keeps_this_week(monkeypatch):
    page = (FIXTURES / "remoterocketship.html").read_text(encoding="utf-8")
    data = json.loads(re.search(r'__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S).group(1))
    jobs = data["props"]["pageProps"]["initialJobOpenings"]  # Huzzle (countries with Egypt), PeakSoft (Worldwide), Soum (Nigeria)
    jobs[0]["created_at"], jobs[1]["created_at"], jobs[2]["created_at"] = TODAY + "T12:36:06+00:00", TODAY + "T09:00:00+00:00", OLD + "T09:00:00+00:00"
    html = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + "</script>"
    asked = []
    monkeypatch.setattr(remote, "get", lambda url, **params: asked.append(url) or Reply(html))
    rows = remote.remoterocketship()
    assert [(r["title"], r["company"]) for r in rows] == [("Data Engineer", "Huzzle.com"), ("AI & Data Engineer, m/f/d", "PeakSoft GmbH")]
    assert "Egypt" in rows[0]["location"] and rows[0]["location"].endswith("(remote)") and rows[1]["location"] == "Worldwide (remote)"
    assert rows[0]["job_url"] == "https://apply.workable.com/huzzle/j/C8C9CAB949" and rows[0]["date_posted"] == TODAY  # the employer's posting
    assert asked[0] == "https://www.remoterocketship.com/country/egypt/jobs/data-engineer/" and len(asked) == len(remote.ROCKETSHIP_TITLES)
