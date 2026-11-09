"""The career sites list grows (settings.yaml companies): a Teamtailor reader, remote-open sites, one broken site never
costing a batch, and a log line that says how many sites were read, left for time, or unreadable. Everything here runs
on saved samples or stand-ins, without the network."""

import time
from pathlib import Path

import pytest

from job_radar import config, steps
from job_radar.sources import base, companies

from test_sources import Saved
from test_limits import web  # noqa: F401  (the `web` fixture)
from test_steps import RolledBack, conn  # noqa: F401  (the `conn` fixture)

FEED = "teamtailor_propertyfinder.xml"  # Property Finder's real jobs.rss, five of its jobs, descriptions shortened


def test_a_teamtailor_address_is_detected_and_read_from_its_rss_with_the_cities_it_names(monkeypatch):
    assert companies.detect("https://propertyfinder.teamtailor.com/jobs") == ("teamtailor", "propertyfinder", None)
    assert companies.detect("https://propertyfinder.teamtailor.com/jobs.rss") == ("teamtailor", "propertyfinder", None)
    asked = []
    monkeypatch.setattr(companies, "get", lambda url, **params: asked.append(url) or Saved(FEED))
    monkeypatch.setattr(companies, "role_of", lambda title: 1)
    rows = companies.READERS["teamtailor"]("Property Finder", "propertyfinder")
    assert asked == ["https://propertyfinder.teamtailor.com/jobs.rss"]
    assert len(rows) == 5  # the Gulf's two too: company_sites, not the reader, keeps only Egypt (and remote) for a site
    egypt = [r for r in rows if config.place_of(r["location"]) == config.HOME]
    assert [(r["title"], r["location"], r["date_posted"][:7]) for r in egypt] == [
        ("Customer Success Partner", "Cairo, Egypt (hybrid)", "2026-07"),
        ("Commercial Strategy & Operations Analyst", "Cairo, Egypt", "2026-10"),
        ("Premium Partner Support Advisor", "Cairo, Egypt", egypt[2]["date_posted"][:7])]
    assert (rows[0]["date_posted"], rows[1]["date_posted"]) == ("2026-07-14", "2026-10-27")
    assert {r["location"] for r in rows} >= {"Dubai, United Arab Emirates", "Manama, Bahrain"}
    assert rows[1]["job_url"] == "https://propertyfinder.teamtailor.com/jobs/8620836-commercial-strategy-operations-analyst"
    assert rows[0]["description"] == "SQL, Python and dashboards for the team"
    assert rows[0]["source"] == "property finder"


def test_a_teamtailor_job_done_fully_remote_is_remote(monkeypatch):
    feed = Saved(FEED).text.replace("<remoteStatus>none</remoteStatus>", "<remoteStatus>fully</remoteStatus>", 1)
    monkeypatch.setattr(companies, "get", lambda url, **params: Saved(feed))
    monkeypatch.setattr(companies, "role_of", lambda title: 1)
    locations = [r["location"] for r in companies.READERS["teamtailor"]("Property Finder", "propertyfinder")]
    assert "Cairo, Egypt (remote)" in locations


JOBS = [{"title": "Data Engineer", "location": "Cairo, Egypt"}, {"title": "Data Engineer", "location": "Dubai, UAE"},
        {"title": "Data Engineer", "location": "Remote, Worldwide"}, {"title": "Data Engineer", "location": "Remote, USA"}]


def test_a_remote_open_site_keeps_its_worldwide_remote_jobs_and_any_other_site_keeps_only_egypt(monkeypatch, web):
    monkeypatch.setitem(companies.READERS, "greenhouse", lambda company, api: JOBS)
    only_egypt, _ = companies.company_sites([("Acme", "https://boards.greenhouse.io/acme", "greenhouse", "acme")])
    assert [r["location"] for r in only_egypt] == ["Cairo, Egypt"]
    both, _ = companies.company_sites([("Acme", "https://boards.greenhouse.io/acme", "greenhouse", "acme", True)])
    assert [r["location"] for r in both] == ["Cairo, Egypt", "Remote, Worldwide"]  # not Dubai, not USA-only remote
    no, _ = companies.company_sites([("Acme", "https://boards.greenhouse.io/acme", "greenhouse", "acme", False)])
    assert [r["location"] for r in no] == ["Cairo, Egypt"]


def test_a_site_whose_detection_raises_is_one_line_in_the_log_and_the_batch_goes_on(monkeypatch, web, capsys):
    """An address detect() cannot even request (a bad link) must not take the other sites of its batch with it."""
    def detect(url):
        if "bad" in url:
            raise ValueError("Invalid URL: no host")
        return "greenhouse", "acme", None
    monkeypatch.setattr(companies, "detect", detect)
    monkeypatch.setitem(companies.READERS, "greenhouse", lambda company, api: [])
    rows, found = companies.company_sites([("Bad", "http://bad link", None, None), ("Good", "https://good.test", None, None)])
    assert found[0] == (None, None, "detection failed: ValueError('Invalid URL: no host')", "Bad")
    assert found[1] == ("greenhouse", "acme", None, "Good")
    assert capsys.readouterr().out.count("Bad") == 1  # one line


def test_fast_sites_come_first_and_the_run_says_how_many_were_read_skipped_and_unreadable(conn, monkeypatch, capsys):
    """extract_companies (rolled back): sites a plain API reads are asked before the ones that need a browser (they are
    cheap, so a slow page never starves them), and the step ends with one line: due, read, skipped for time, unreadable.
    A site not yet detected whose address names its platform (a Greenhouse board) is as cheap as one already known."""
    monkeypatch.setattr(steps, "online", lambda: True)
    monkeypatch.setitem(config.FEATURES, "eligibility", False)
    for name, platform, hours, url in (
            ("zz page old", "page", 90, None), ("zz api new", "greenhouse", 22, None), ("zz page new", "page", 30, None),
            ("zz api old", "lever", 40, None), ("zz none", None, 95, None), ("zz blocked", "blocked", 99, None),
            ("zz new board", None, 96, "https://boards.greenhouse.io/zz"), ("zz held", "greenhouse", 21, None)):
        conn.execute("INSERT INTO core.company (company, careers_url, platform, checked_at) VALUES (%s, %s, %s,"
                     " now() - make_interval(hours => %s))", (name, url or f"https://test.example/{name}", platform, hours))
    batches = []

    def company_sites(sites, threads=4):
        batches.append([s[0] for s in sites])
        notes = {"zz blocked": ("blocked", "it shows a bot check"), "zz held": (None, "read failed: Held('x.test is held for 2 hours more')"),
                 "zz new board": ("greenhouse", None)}  # detected from its address
        return [], [(notes.get(s[0], (s[2], None))[0], s[3], notes.get(s[0], (s[2], None))[1], s[0]) for s in sites]

    monkeypatch.setattr(steps.sources, "company_sites", company_sites)
    steps.extract_companies(RolledBack(conn))
    asked = [name for batch in batches for name in batch if name.startswith("zz")]
    assert asked.index("zz api old") < asked.index("zz page old") and asked.index("zz api new") < asked.index("zz page new")
    assert asked.index("zz api old") < asked.index("zz api new")  # each kind, oldest read first
    assert asked.index("zz new board") < asked.index("zz page new") and asked.index("zz new board") < asked.index("zz none")
    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if l.startswith("extract_companies: ") and " due, " in l)
    assert "8 due, 5 read, 0 skipped for time, 3 unreadable (1 blocked, 1 held, 1 not reached)" in line


def test_sites_left_for_time_are_counted_and_are_first_in_line_next_run(conn, monkeypatch, capsys):
    monkeypatch.setattr(steps, "online", lambda: True)
    for i in range(3):
        conn.execute("INSERT INTO core.company (company, careers_url, platform) VALUES (%s, %s, 'greenhouse')",
                     (f"zz late {i}", f"https://test.example/late{i}"))
    monkeypatch.setattr(base, "STARTED", time.monotonic() - config.EXTRACT_SECONDS + 30)  # 30 s left: below the 70 s a batch needs
    monkeypatch.setattr(steps.sources, "company_sites", lambda sites, threads=4: pytest.fail("no batch may start"))
    steps.extract_companies(RolledBack(conn))
    line = next(l for l in capsys.readouterr().out.splitlines() if l.startswith("extract_companies: ") and " due, " in l)
    assert "3 due, 0 read, 3 skipped for time" in line


def test_the_remote_flag_in_settings_reaches_core_company(conn, monkeypatch):
    """schema (rolled back): `remote: true` on a company in settings.yaml is the column the reader asks for."""
    monkeypatch.setitem(steps.SETTINGS, "companies", [{"name": "zz Open", "link": "https://zz.test/careers", "remote": True},
                                                       {"name": "zz Home", "link": "https://zz2.test/careers"}])
    steps.schema(RolledBack(conn))
    got = dict(conn.execute("SELECT company, remote FROM core.company WHERE company LIKE 'zz %'").fetchall())
    assert got == {"zz Open": True, "zz Home": False}


def test_every_company_in_settings_has_a_name_a_link_and_one_entry_only():
    """The list grows by the hundred: a duplicate name, a repeated board, a missing link or an unknown key is caught here."""
    import re
    entries = config.SETTINGS["companies"]
    names = [re.sub(r"[^a-z0-9]", "", c["name"].lower()) for c in entries]
    assert len(names) == len(set(names)), sorted({n for n in names if names.count(n) > 1})
    assert all(c.get("link", "").startswith("https://") for c in entries), [c["name"] for c in entries if not c.get("link", "").startswith("https://")]
    assert {k for c in entries for k in c} <= {"name", "link", "starred", "remote", "note"}
    assert not [c["name"] for c in entries if "|" in c.get("note", "") or "|" in c["name"]]  # a pipe would break docs/companies.md's table
    assert all(isinstance(c.get("remote", False), bool) for c in entries)
    boards = []
    for c in entries:
        for platform, pattern in companies.ATS_PATTERNS:
            if m := re.search(pattern, c["link"]):
                boards.append((platform, m.group(1).lower()))
    assert len(boards) == len(set(boards)), sorted({b for b in boards if boards.count(b) > 1})


def test_a_rendered_page_does_not_download_its_images_and_fonts_but_runs_its_scripts(web):
    """rendered() reads links, not pictures: with them blocked a career page loads about a quarter faster (measured on ten
    sites, 94 s against 72 s, with the same links), and the freed browser time is what the added sites are read in."""
    import http.server
    import threading

    asked = []

    class Site(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            asked.append(self.path)
            body = {"/": b"<html><body><img src='/pic.png'><script>document.body.insertAdjacentHTML("
                         b"'beforeend', '<a href=/jobs/1>Data Engineer</a>')</script></body></html>"}.get(self.path, b"x")
            self.send_response(200)
            self.send_header("Content-Type", "text/html" if self.path == "/" else "application/octet-stream")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        html = base.rendered(f"http://127.0.0.1:{server.server_port}/")
    finally:
        server.shutdown()
        if hasattr(base.BROWSERS, "chromium"):
            base.BROWSERS.chromium.close()
            del base.BROWSERS.chromium
    assert "Data Engineer" in html  # the script ran
    assert "/pic.png" not in asked  # the picture was never asked for


def test_home_based_worldwide_is_remote_open_to_egypt_but_home_based_in_one_region_is_not():
    """Canonical writes 'Home based - Worldwide' for its remote jobs (93 of its roles, 2026-11-09): no word 'remote', so
    place_of dropped them all."""
    assert config.place_of("Home based - Worldwide") == "Remote"
    assert config.place_of("Home Based - Americas; Home based - EMEA; Home based - Worldwide") == "Remote"
    assert config.place_of("Home Based - EMEA") == "Remote"  # EMEA is open to someone in Egypt, as for 'Remote - EMEA'
    assert config.place_of("Home Based - Americas") is None
    assert config.place_of("Home based - Germany") == config.place_of("Remote - Germany")
