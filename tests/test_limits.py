r"""The rate limits and the LinkedIn alert rule, without the network. Run on the laptop:

    .venv\Scripts\python -m pytest tests     (once: .venv\Scripts\pip install pytest)
"""

import json
import threading
import time

import pytest

from job_radar import config, sources


class Answer:
    def __init__(self, status, headers=None):
        self.status_code, self.headers, self.text = status, headers or {}, ""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise sources.base.requests.HTTPError(str(self.status_code))


@pytest.fixture
def web(monkeypatch, tmp_path):
    """Both clients answer from `web.plain` / `web.chrome`; every call is noted in `web.calls`."""
    monkeypatch.setattr(sources.base, "WAITS", tmp_path / "waits")
    monkeypatch.setattr(sources.base, "SESSIONS", tmp_path / "sessions")
    monkeypatch.setattr(sources.base, "NEXT_TURN", {})
    monkeypatch.setattr(sources.base, "CHROME_ONLY", set())
    web = type("Web", (), {"plain": Answer(200), "chrome": Answer(200), "calls": []})()

    def client(name):
        def answer(method, url, **kw):
            web.calls.append((name, time.monotonic(), kw))
            if isinstance(getattr(web, name), Exception):
                raise getattr(web, name)
            return getattr(web, name)
        return answer
    monkeypatch.setattr(sources.base.requests, "request", client("plain"))
    monkeypatch.setattr(sources.base.chrome_requests, "request", client("chrome"))
    return web


def test_one_request_a_second_per_host_across_threads(web):
    threads = [threading.Thread(target=sources.fetch, args=("GET", "https://a.test/x")) for _ in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    starts = sorted(at for _, at, _ in web.calls)
    assert all(b - a >= 0.99 for a, b in zip(starts, starts[1:]))
    began = time.monotonic()
    sources.fetch("GET", "https://b.test/x")  # another host does not wait
    assert time.monotonic() - began < 0.9  # under the 1 s a held turn would cost, even on a busy laptop


def test_403_is_asked_once_as_chrome_then_held_6_hours(web):
    web.plain = web.chrome = Answer(403)
    with pytest.raises(sources.Held):
        sources.fetch("GET", "https://c.test/x")
    assert [client for client, _, _ in web.calls] == ["plain", "chrome"]
    assert "User-Agent" not in web.calls[1][2]["headers"]  # curl_cffi sends its own Chrome's
    assert 5.9 * 3600 < sources.base.waiting("c.test") <= 6 * 3600
    web.calls.clear()
    with pytest.raises(sources.Held):
        sources.fetch("GET", "https://c.test/y")
    assert web.calls == []  # a held host is not asked


def test_chrome_answer_is_used_when_it_gets_through(web):
    web.plain = Answer(403)
    assert sources.fetch("GET", "https://d.test/x") is web.chrome
    assert sources.base.waiting("d.test") == 0


@pytest.mark.parametrize("error", [sources.base.requests.Timeout(), sources.base.requests.exceptions.SSLError()])
def test_a_site_that_never_answers_or_drops_the_handshake_is_asked_as_chrome(web, error):
    web.plain = error
    assert sources.fetch("GET", "https://g.test/x", timeout=30) is web.chrome
    first, second = web.calls
    assert first[2]["timeout"] == 15  # requests gets half, curl_cffi what is left: both within the 30 s
    assert second[1] - first[1] + second[2]["timeout"] <= 30.1


def test_a_site_only_chrome_gets_through_is_asked_as_chrome_from_then_on(web):
    web.plain = sources.base.requests.exceptions.ConnectionError()
    sources.fetch("GET", "https://k.test/1")
    sources.fetch("GET", "https://k.test/2")
    assert [client for client, _, _ in web.calls] == ["plain", "chrome", "chrome"]


def test_a_refused_job_page_does_not_hold_its_host(web):
    web.plain = web.chrome = Answer(403)
    with pytest.raises(sources.Held):
        sources.fetch("GET", "https://apply.test/j/1", hold_on_403=False)
    assert sources.base.waiting("apply.test") == 0


def test_detect_keeps_to_holds_and_holds_on_429(web):
    sources.base.hold("h.test", 3600)
    assert sources.companies.detect("https://h.test/careers")[0] is None and web.calls == []
    web.plain = Answer(429, {"Retry-After": "600"})
    assert sources.companies.detect("https://i.test/careers")[0] is None and sources.base.waiting("i.test") > 500


def test_only_a_refusal_resets_a_sites_platform(monkeypatch, web):
    def fails(error):
        monkeypatch.setitem(sources.companies.READERS, "rss", lambda company, api: (_ for _ in ()).throw(error))
        return sources.company_sites([("Acme", "https://j.test/feed", "rss", "https://j.test/feed")])[1][0][0]
    assert fails(sources.Held("held")) is None and fails(sources.Challenge("bot check")) is None
    assert fails(TimeoutError("slow")) == "rss"


def test_429_is_held_for_its_retry_after_without_a_chrome_retry(web):
    web.plain = Answer(429, {"Retry-After": "120"})
    with pytest.raises(sources.Held):
        sources.fetch("GET", "https://e.test/x")
    assert [client for client, _, _ in web.calls] == ["plain"]
    assert 100 < sources.base.waiting("e.test") <= 120


def test_a_saved_session_sends_its_cookies_and_user_agent(web):
    sources.base.SESSIONS.mkdir()
    (sources.base.SESSIONS / "f.test.json").write_text(json.dumps(
        {"user_agent": "UA-X", "cookies": [{"name": "cf_clearance", "value": "v", "domain": ".f.test"}]}))
    sources.fetch("GET", "https://f.test/x")
    sent = web.calls[0][2]
    assert sent["headers"]["User-Agent"] == "UA-X" and sent["cookies"] == {"cf_clearance": "v"}


def test_every_job_in_a_linkedin_alert_is_kept():
    page = """<a href="https://www.linkedin.com/comm/jobs/view/111?trk=x"><img></a>
    <a href="https://www.linkedin.com/comm/jobs/view/111?trk=x">Costing Analyst<br>Kraft Heinz · Qesm 2nd 6 October</a>
    <a href="https://www.linkedin.com/comm/jobs/view/222">Junior Data Analyst<br>MetLife Egypt · Cairo (Hybrid)</a>
    <a href="https://www.linkedin.com/comm/jobs/view/333">Data Entry Clerk<br>X · Cairo</a>
    <a href="https://www.linkedin.com/jobs/search?keywords=data">See all jobs</a>
    <a href="https://example.com/jobs/444">Costing Analyst</a>"""
    assert [job[0] for job in sources.inbox.jobs_in_email(page, alert=True)] == ["Costing Analyst", "Junior Data Analyst"]
    assert [job[0] for job in sources.inbox.jobs_in_email(page)] == ["Junior Data Analyst"]  # not from LinkedIn's alerts
    assert config.role_of("Costing Analyst", alert=True) == config.ALERT_RANK
    assert config.role_of("Costing Analyst") is None and config.role_of("", alert=True) is None
    assert list(config.ROLE_LABEL)[-1] == config.ALERT_RANK  # listed after your roles


def test_a_remote_board_keeps_your_roles_from_places_in_scope_this_week():
    today = time.strftime("%Y-%m-%d")
    jobs = [{"t": "Data Engineer", "geo": "Anywhere", "u": "https://x.test/1", "d": today},
            {"t": "Data Engineer", "geo": "USA Only", "u": "https://x.test/2", "d": str(int(time.time()))},  # epoch
            {"t": "Data Engineer", "geo": "Anywhere", "u": "https://x.test/3", "d": "2020-01-01"},  # too old
            {"t": "Copywriter", "geo": "Anywhere", "u": "https://x.test/4", "d": today}]  # not your role
    rows = sources.remote.remote_board("board", jobs, "t", "c", "geo", "u", "d", "desc")
    assert [(r["job_url"], config.place_of(r["location"])) for r in rows] == [("https://x.test/1", "Remote"),
                                                                             ("https://x.test/2", "USA")]


def test_company_sites_keep_only_their_jobs_in_egypt(monkeypatch, web):
    jobs = [{"title": "Data Engineer", "location": "Cairo, Egypt"}, {"title": "Data Engineer", "location": "Dubai, UAE"},
            {"title": "Data Engineer", "location": "Remote, Worldwide"}]
    monkeypatch.setitem(sources.companies.READERS, "greenhouse", lambda company, api: jobs)
    rows, _ = sources.company_sites([("Acme", "https://boards.greenhouse.io/acme", "greenhouse", "acme")])
    assert [r["location"] for r in rows] == ["Cairo, Egypt"]


def test_6_october_is_an_onsite_area():
    assert config.place_of("Qesm 2nd 6 October") == config.HOME
    assert config.in_reach(config.HOME, "Qesm 2nd 6 October", "Costing Analyst")
