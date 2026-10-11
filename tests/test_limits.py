r"""The rate limits and the LinkedIn alert rule, without the network. Run on the laptop:

    .venv\Scripts\python -m pytest tests     (once: .venv\Scripts\pip install pytest)
"""

import gzip
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


def test_linkedin_runs_apify_once_then_holds_for_every_hours(monkeypatch, web):
    posts = []

    class Done(Answer):
        def json(self):
            return []
    monkeypatch.setenv("APIFY_TOKEN", "test-token")
    monkeypatch.setattr(sources.boards.requests, "post", lambda *a, **kw: posts.append(kw) or Done(200))
    sources.boards.linkedin()
    sources.boards.linkedin()  # a manual collect the same day: no second paid run
    assert len(posts) == 1
    assert sources.base.waiting("api.apify.com") > (config.SETTINGS["linkedin"]["every_hours"] - 1) * 3600


def test_a_site_still_reading_when_the_budget_ends_is_stamped_and_one_never_started_is_left_first_in_line(monkeypatch, web):
    """company_sites returns at the batch's end whatever a site is doing, and its thread is left behind (__main__ leaves
    without it). A site still loading comes back with a note, so its checked_at moves and the next run reads the others
    (unstamped, the same slow sites came first every run, 2026-10-11); a site the 4 threads never started does not, so
    it stays first in line."""
    stuck = threading.Event()
    monkeypatch.setitem(sources.companies.READERS, "rss", lambda company, api: [] if company == "Quick" or not stuck.wait(30) else [])
    monkeypatch.setattr(sources.base, "STARTED", time.monotonic() + sources.companies.SITE_SECONDS + 1.5 - config.EXTRACT_SECONDS)
    began = time.monotonic()
    names = ["Quick", "Slow 1", "Slow 2", "Slow 3", "Slow 4", "Never started"]  # 4 threads: Quick's frees one for Slow 4
    try:
        rows, found = sources.company_sites([(name, f"https://{i}.test/feed", "rss", "x") for i, name in enumerate(names)])
        assert time.monotonic() - began < 5  # the batch's 1.5 s, not the stuck sites' 30
        assert rows == [] and found[0] == ("rss", "x", None, "Quick")
        assert found[1:] == [("rss", "x", "not read: the run's time ran out while it loaded", f"Slow {n}") for n in (1, 2, 3, 4)]
    finally:
        stuck.set()


def test_a_request_after_the_sites_time_is_spent_is_not_sent(web):
    sources.base.SITE.until = time.monotonic() - 1
    try:
        with pytest.raises(sources.base.requests.Timeout):
            sources.fetch("GET", "https://slow.test/x")
        assert web.calls == []
        sources.base.SITE.until = time.monotonic() + 5  # a site with 5 s left: no request may wait longer
        sources.fetch("GET", "https://slow.test/x", timeout=60)
        assert web.calls[0][2]["timeout"] <= 2.5  # requests gets half of what is left
    finally:
        del sources.base.SITE.until


def test_a_browser_wait_is_never_under_a_second_for_the_site_time_left_is_raised_before():
    """Playwright gets site_left(cap) * 1000: at least 1000 ms or the Timeout raised here, never a zero or a negative."""
    try:
        sources.base.SITE.until = time.monotonic() + 0.5
        with pytest.raises(sources.base.requests.Timeout):
            sources.base.site_left(30)
        sources.base.SITE.until = time.monotonic() + 1.5
        assert 1 <= sources.base.site_left(30) <= 1.5
        assert sources.base.site_left(1) == 1  # the cap still wins when it is smaller
    finally:
        del sources.base.SITE.until


FEED = {"last_updated": "v1", "chunks": ["c0.gz", "c1.gz", "c2.gz", "c3.gz"]}


@pytest.fixture
def portals(monkeypatch, tmp_path):
    """The 28,000-company feed from memory: 4 chunks of one Egypt job each, read one at a time; `portals.read` lists the
    chunks downloaded, `portals.budget` is the seconds of the step's time budget left (a function of the chunks read)."""
    today = time.strftime("%Y-%m-%d")
    box = type("Portals", (), {"read": [], "feed": dict(FEED), "failing": set(), "budget": lambda self: 100})()

    def job(name):
        return gzip.compress(json.dumps([{"ats": "Lever", "title": "Data Engineer", "company": "Acme", "location": "Cairo, Egypt",
                                          "url": f"https://acme.test/{name}", "first_seen": today}]).encode())

    def fake_fetch(method, url, **kw):
        name = url.rsplit("/", 1)[1]
        box.read.append(name)
        if name in box.failing:
            raise sources.base.requests.ConnectionError("down")
        assert kw["timeout"] == sources.companies.CHUNK_SECONDS
        return type("Response", (), {"content": job(name)})()

    monkeypatch.setattr(sources.companies, "FEED_READ", tmp_path / "feed.txt")
    monkeypatch.setattr(sources.companies, "CHUNK_THREADS", 1)  # one at a time: the budget below is checked chunk by chunk
    monkeypatch.setattr(sources.companies, "get", lambda url, **p: type("Response", (), {"json": lambda self: box.feed})())
    monkeypatch.setattr(sources.companies, "fetch", fake_fetch)
    monkeypatch.setattr(sources.companies, "time_left", lambda budget: box.budget())
    return box


def test_a_portal_feed_that_runs_out_of_time_keeps_what_it_read_and_the_next_run_continues(portals, capsys):
    portals.budget = lambda: 100 if len(portals.read) < 2 else 10  # under CHUNK_SECONDS once two chunks are read
    first = sources.company_portals()
    assert portals.read == ["c0.gz", "c1.gz"] and [r["job_url"] for r in first] == ["https://acme.test/c0.gz", "https://acme.test/c1.gz"]
    assert "company_portals: read 2 of 4 chunks (feed v1)" in capsys.readouterr().out
    assert json.loads(sources.companies.FEED_READ.read_text()) == {"version": "v1", "chunks": ["c0.gz", "c1.gz"]}
    portals.read.clear()
    portals.budget = lambda: 100
    second = sources.company_portals()  # resumes: only the chunks not read, so no job twice
    assert portals.read == ["c2.gz", "c3.gz"] and [r["job_url"] for r in second] == ["https://acme.test/c2.gz", "https://acme.test/c3.gz"]
    assert "company_portals: read 4 of 4 chunks (feed v1)" in capsys.readouterr().out
    portals.read.clear()
    assert sources.company_portals() == [] and portals.read == []  # whole feed read: nothing downloaded
    assert "unchanged since v1" in capsys.readouterr().out


def test_a_new_feed_version_or_an_old_state_file_reads_every_chunk(portals):
    sources.company_portals()
    assert len(portals.read) == 4
    portals.read.clear()
    portals.feed = {**FEED, "last_updated": "v2"}
    assert len(sources.company_portals()) == 4 and len(portals.read) == 4  # a new version: all again
    portals.read.clear()
    sources.companies.FEED_READ.write_text("v2")  # the bare time the file held before chunks were remembered
    assert len(sources.company_portals()) == 4 and len(portals.read) == 4


def test_a_chunk_that_fails_is_not_marked_read_and_the_others_are_kept(portals, capsys):
    portals.failing = {"c1.gz"}
    rows = sources.company_portals()
    assert sorted(r["job_url"][-5:] for r in rows) == ["c0.gz", "c2.gz", "c3.gz"]
    assert "WARNING company_portals: c1.gz" in capsys.readouterr().out
    assert json.loads(sources.companies.FEED_READ.read_text())["chunks"] == ["c0.gz", "c2.gz", "c3.gz"]
    portals.failing, portals.read = set(), []
    assert [r["job_url"][-5:] for r in sources.company_portals()] == ["c1.gz"] and portals.read == ["c1.gz"]
