"""Inclusively Remote (WordPress REST API) and Hireeing (list pages and each job's JSON-LD), read from saved real responses."""
import json
from datetime import date, timedelta
from pathlib import Path

from job_radar.sources import remote

FIXTURES = Path(__file__).parent / "fixtures"


class Reply:
    def __init__(self, text):
        self.text = text

    def json(self):
        return json.loads(self.text)


def test_inclusivelyremote_keeps_your_roles_in_scope_posted_this_week(monkeypatch):
    jobs = json.loads((FIXTURES / "inclusivelyremote.json").read_text(encoding="utf-8"))
    today, old = date.today().isoformat() + "T08:00:00", (date.today() - timedelta(days=20)).isoformat() + "T08:00:00"
    jobs[0]["date"], jobs[1]["date"], jobs[2]["date"] = today, old, today  # Data Engineer, Senior Data Analyst (old), SDR
    pages = [jobs, []]
    asked = []
    monkeypatch.setattr(remote, "get", lambda url, **params: asked.append((url, params)) or Reply(json.dumps(pages.pop(0))))
    rows = remote.inclusivelyremote()
    assert [r["title"] for r in rows] == ["Data Engineer"]  # the analyst is 20 days old, the SDR is no role of yours
    r = rows[0]
    assert (r["source"], r["company"], r["location"]) == ("inclusivelyremote", "Beehiiv", "Remote Jobs Worldwide (remote)")
    assert r["job_url"].startswith("https://inclusivelyremote.com/job/") and r["date_posted"] == date.today().isoformat()
    assert r["payload"]["apply_url"] == "https://beehiiv.bamboohr.com/careers/76" and "<" not in r["description"]
    assert asked[0][0] == "https://inclusivelyremote.com/wp-json/wp/v2/job_listing" and asked[0][1]["per_page"] == 100
    assert len(asked) == 1  # page 1 holds a job older than a week: no page 2


def hireeing_pages(monkeypatch, countries, posted=None):
    listing = (FIXTURES / "hireeing_list.html").read_text(encoding="utf-8")
    page = (FIXTURES / "hireeing_job.html").read_text(encoding="utf-8")
    job = json.loads(page.split('ld+json">')[1].split("</script>")[0])
    job["datePosted"] = (posted or date.today()).isoformat() + "T08:00:00.000Z"
    job["applicantLocationRequirements"] = [{"@type": "Country", "name": c} for c in countries]
    html = '<script type="application/ld+json">' + json.dumps(job) + "</script>"
    asked = []

    def fake_get(url, **params):
        asked.append(url)
        return Reply(listing if url.endswith("/jobs") else html)

    monkeypatch.setattr(remote, "get", fake_get)
    monkeypatch.setattr(remote.time, "sleep", lambda seconds: None)
    return asked


def test_hireeing_reads_each_roles_search_then_the_job_pages_it_has_not_stored(monkeypatch):
    asked = hireeing_pages(monkeypatch, [])  # no country named: open worldwide
    rows = remote.hireeing(set())
    assert rows and {r["source"] for r in rows} == {"hireeing"}
    r = rows[0]
    assert r["job_url"].startswith("https://hireeing.com/jobs/") and r["location"] == "Worldwide (remote)"
    assert r["date_posted"] == date.today().isoformat() and r["company"] and r["description"]
    stored = {r["job_url"] for r in rows}
    asked.clear()
    assert remote.hireeing(stored) == [] and not [u for u in asked if u in stored]  # a stored job page is not read again


def test_hireeing_keeps_the_country_a_job_names_and_drops_an_old_posting(monkeypatch):
    hireeing_pages(monkeypatch, ["United States"])
    assert remote.hireeing(set())[0]["location"] == "United States (remote)"
    hireeing_pages(monkeypatch, [], posted=date.today() - timedelta(days=30))
    assert remote.hireeing(set()) == []
