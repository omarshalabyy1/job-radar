r"""Who can take a job (a board's who-can-apply field) and what the emails show of Claude's grade, without the network
or the warehouse. Run on the laptop:

    .venv\Scripts\python -m pytest tests
"""

from datetime import date, datetime, timezone

import pytest

from job_radar import config, digest, eligibility, runlog, steps


@pytest.mark.parametrize("where", [
    "Worldwide", "Anywhere in the World", "Worldwide (remote)", "Remote: EMEA", "MENA", "Africa", "Middle East",
    "Egypt (Remote)", "Egypt, Nigeria, Pakistan (Remote)", "Global", "UTC+2", "GMT+3", "UTC-1 to UTC+4", "EET",
    "Algeria, Angola, Egypt, Ghana"])
def test_a_board_that_lets_someone_in_cairo_apply_passes(where):
    assert eligibility.board_restriction(where) is None


@pytest.mark.parametrize("where, found", [
    ("United States (remote)", "open to United States only"), ("Remote: Canada, United Kingdom", "open to Canada, United Kingdom only"),
    ("USA", "open to USA only"), ("Europe", "open to Europe only"), ("LATAM,  Canada,  USA (remote)", "open to LATAM,  Canada,  USA only"),
    ("South Africa", "open to South Africa only"), ("UTC-8 to UTC-5", "open to UTC-8 to UTC-5 only"), ("US National (remote)", "open to US National only")])
def test_a_board_limited_to_other_countries_restricts_the_job(where, found):
    assert eligibility.board_restriction(where) == found


@pytest.mark.parametrize("source, location, payload, expected", [
    ("dailyremote", "United States (remote)", {}, "United States (remote)"),  # the boards whose location is the field
    ("remotive", "USA (remote)", {}, "USA (remote)"),
    ("wellfound", "Remote: Berlin", {"acceptedRemoteLocationNames": ["Germany", "Brazil"]}, "Germany, Brazil"),
    ("wellfound", "Remote: Berlin", {"locationNames": ["Berlin"]}, None),  # its offices only: no field
    ("welcometothejungle", "Remote: France", {}, None),  # office countries, not who can apply
    ("indeed", "Remote", {}, None)])
def test_only_a_boards_own_who_can_apply_field_is_read(source, location, payload, expected):
    assert eligibility.who_can_apply(source, location, payload) == expected


def job(n, score, reason="A deciding match"):
    return {"job_id": n, "title": f"Data Engineer {n}", "company": f"Acme {n}", "location": "Cairo, Egypt", "place": "Egypt",
            "source": "indeed", "job_url": f"https://example.com/{n}", "role_rank": 1, "target_company": False,
            "date_posted": None, "description": None, "job_type": None, "score": score, "grade_reason": reason}


def test_the_email_shows_the_score_the_reason_and_the_top_five():
    jobs = [job(n, score) for n, score in enumerate([9, 8, 7, 7, 8, 9, 7, 5, None], 1)]
    body = digest.digest(jobs, "Egypt")
    assert "Score 9/10" in body and "A deciding match" in body
    top = body.split("Top 5 today")[1].split("</td></tr>")[0]  # the block is one row
    linked = [n for n in range(1, 10) if f">Data Engineer {n}<" in top]
    assert linked == [1, 2, 3, 5, 6]  # the two 9s, the two 8s and the first 7; 7 7, 5 and no score are left out


def test_no_job_of_7_or_more_means_no_top_five_block():
    assert "Top 5 today" not in digest.digest([job(1, 6), job(2, None)], "Egypt")


def test_the_follow_up_block_lists_the_rows_and_is_absent_without_any():
    rows = [{"company": "Acme", "title": "Data Analyst", "applied_at": datetime(2026, 10, 3, tzinfo=timezone.utc)}]
    body = digest.digest([job(1, 8)], "Egypt", rows)
    assert "Follow up" in body and "Data Analyst at <b>Acme</b>, applied 03 Oct" in body
    assert "Follow up" not in digest.digest([job(1, 8)], "Egypt", [])


@pytest.mark.parametrize("switch, shown", [("top5", "Top 5 today"), ("follow_up", "Follow up")])
def test_a_switched_off_block_is_gone(monkeypatch, switch, shown):
    monkeypatch.setitem(config.FEATURES, switch, False)
    rows = [{"company": "Acme", "title": "Data Analyst", "applied_at": datetime(2026, 10, 3, tzinfo=timezone.utc)}]
    assert shown not in digest.digest([job(1, 8)], "Egypt", rows)


def test_every_smart_feature_has_a_switch_in_settings():
    assert {"grade", "eligibility", "auto_hide", "top5", "follow_up", "follow_up_drafts", "tailor", "track_applications",
            "interview_prep", "embeddings", "manual_collect_emails"} <= set(config.FEATURES)
    assert all(isinstance(on, bool) for on in config.FEATURES.values())


ON = {"auto_hide": True, "eligibility": True, "grade": True}


@pytest.mark.parametrize("eligibility, auto_hide, expected", [
    (True, True, "US citizens only"),   # both hide: the restriction is the reason shown
    (True, False, "US citizens only"),  # eligibility alone hides a restriction, whatever auto_hide says
    (False, True, "score 2"),           # auto_hide hides only the low score
    (False, False, None)])              # nothing hides
def test_eligibility_and_auto_hide_work_independently(eligibility, auto_hide, expected):
    switches = {"grade": True, "eligibility": eligibility, "auto_hide": auto_hide}
    assert config.hidden_reason("new", "US citizens only", 2, switches) == expected
    assert config.hidden_reason("new", None, 2, switches) == ("score 2" if auto_hide else None)
    assert config.hidden_reason("new", "US citizens only", 9, switches) == ("US citizens only" if eligibility else None)


@pytest.mark.parametrize("status, restriction, score, switches, expected", [
    ("new", "US citizens only", 9, ON, "US citizens only"),
    ("new", None, 3, ON, "score 3"),
    ("new", None, 3.0, ON, "score 3"),                                         # the tracker's pandas floats
    ("new", None, 4, ON, None),
    ("new", None, float("nan"), ON, None),                                      # not graded, in pandas
    ("new", float("nan"), None, ON, None),                                      # no restriction, in pandas
    ("saved", "US citizens only", 2, ON, None),                                 # your status wins
    ("new", "US citizens only", 2, {**ON, "auto_hide": False}, "US citizens only"),  # a restriction needs eligibility alone
    ("new", None, 2, {**ON, "auto_hide": False}, None),                         # a low score needs auto_hide
    ("new", "US citizens only", 9, {**ON, "eligibility": False}, None),         # restrictions ignored
    ("new", "US citizens only", 2, {**ON, "eligibility": False}, "score 2"),    # the score still counts
    ("new", None, 2, {**ON, "grade": False}, None),                             # scores ignored
    ("new", "US citizens only", 2, {**ON, "grade": False}, "US citizens only"),
])
def test_hidden_reason_follows_the_switches(status, restriction, score, switches, expected):
    assert config.hidden_reason(status, restriction, score, switches) == expected


class Warehouse:
    """Just enough of a psycopg connection for steps.transform: it returns the given postings as the window,
    the raw postings kept (kept, else the same) for the text lookup, no company and no deleted job, and keeps the
    jobs transform would store."""

    def __init__(self, postings, kept=None):
        self.postings, self.kept, self.stored, self.rowcount = postings, kept or postings, [], 0
        self.sql, self.params = "", None

    def cursor(self, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sql, self.params = sql, params
        if "description <> ''" in sql:
            self.text_days = params[0]
        return self

    def fetchall(self):
        return self.postings

    def __iter__(self):  # the text lookup (title, company, link, description of the raw postings kept); nothing else is iterated
        return iter([(p["title"], p["company"], p["job_url"], p["description"]) for p in self.kept if p["description"]]
                    if "description <> ''" in self.sql else [])

    def executemany(self, sql, jobs):
        self.stored = jobs

    def commit(self):
        pass


def posting(n, description):
    return {"run_date": date(2026, 10, 10), "source": "indeed", "searched_for": "Egypt", "title": f"Data Engineer {n}",
            "company": f"Acme {n}", "location": "Cairo, Egypt", "job_url": f"https://example.com/{n}", "date_posted": None,
            "description": description, "is_remote": False, "sender": None, "payload": {}, "loaded_at": datetime(2026, 10, 10, tzinfo=timezone.utc)}


@pytest.mark.parametrize("eligibility", [True, False])
def test_transform_stores_a_posting_that_says_it_bars_egypt_for_match_skills_to_hide(monkeypatch, eligibility):
    """A barred posting is a job like any other here, with either switch: match_skills sets its restriction and the
    email and the tracker hide it, so Show hidden jobs can bring it back and a rerun never loses it."""
    monkeypatch.setitem(config.FEATURES, "eligibility", eligibility)
    conn = Warehouse([posting(1, "You must be a U.S. citizen."), posting(2, "Based in Egypt, SQL and Airflow."), posting(3, None)])
    steps.transform(conn)
    assert [job["title"] for job in conn.stored] == ["Data Engineer 1", "Data Engineer 2", "Data Engineer 3"]


def test_transform_gives_a_job_the_text_of_the_same_job_from_a_posting_that_is_not_kept(monkeypatch):
    monkeypatch.setitem(config.FEATURES, "eligibility", True)
    alert = {**posting(1, None), "source": "email", "title": "Margin assurance analyst", "job_url": "https://www.linkedin.com/jobs/view/1",
             "sender": "jobs-listings@linkedin.com"}  # a LinkedIn alert keeps any title
    indeed = {**posting(1, "Based in Egypt, SQL and Airflow."), "title": "Margin assurance analyst"}  # no role of yours in the title: not kept itself
    conn = Warehouse([alert, indeed])
    steps.transform(conn)
    assert [(job["source"], job["description"]) for job in conn.stored] == [("email", "Based in Egypt, SQL and Airflow.")]


def test_transform_takes_the_text_from_a_kept_posting_older_than_the_window(monkeypatch):
    monkeypatch.setitem(config.FEATURES, "eligibility", True)
    alert = {**posting(1, None), "source": "email", "title": "Margin assurance analyst", "job_url": "https://www.linkedin.com/jobs/view/1",
             "sender": "jobs-listings@linkedin.com"}  # today's LinkedIn alert: no text
    indeed = {**posting(1, "Based in Egypt, SQL and Airflow."), "title": "Margin assurance analyst",
              "run_date": date(2026, 10, 8)}  # the same job's Indeed page of 3 days ago: outside the 1-day window, inside KEEP_DAYS
    conn = Warehouse([alert], kept=[alert, indeed])
    steps.transform(conn)
    assert [(job["source"], job["description"]) for job in conn.stored] == [("email", "Based in Egypt, SQL and Airflow.")]
    assert conn.text_days == config.KEEP_DAYS  # the lookup spans the days raw postings are kept, not the 1-day window


def test_transform_counts_the_postings_it_leaves_out_by_rule(monkeypatch, capsys):
    monkeypatch.setitem(config.FEATURES, "eligibility", True)
    senior = {**posting(1, None), "title": "Principal Data Engineer"}
    no_role = {**posting(2, None), "title": "Barista"}
    abroad = {**posting(3, None), "searched_for": "Germany", "location": "Berlin, Germany"}  # onsite outside home
    barred = posting(4, "You must be a U.S. citizen.")  # kept: hidden later, not left out
    steps.transform(Warehouse([senior, no_role, abroad, barred, posting(5, None)]))
    out = capsys.readouterr().out
    assert "2 jobs in scope" in out and "postings left out: 1 no role, 1 too senior, 1 not in reach" in out and "restricted" not in out
    assert len(out.strip()) + 3 * 7 <= runlog.LINE_CHARS  # the run log keeps this line whole, even with four-digit counts (7 of them)
