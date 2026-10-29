"""publish retries a Neon deadlock or serialization failure, and gives up after RETRIES; clean_url strips an alert link's tokens;
publish_once's prunes and the GitHub run's skips, against a fake warehouse and a fake Neon that record the SQL (no database)."""

import urllib.error

import psycopg
import pytest

from job_radar import publish


def run(monkeypatch, errors):
    """publish() with publish_once raising each of `errors` in turn, then succeeding; returns the attempts made."""
    calls = []

    def once(conn):
        calls.append(conn)
        if len(calls) <= len(errors):
            raise errors[len(calls) - 1]

    monkeypatch.setattr(publish, "publish_once", once)
    monkeypatch.setattr(publish.time, "sleep", lambda s: None)
    monkeypatch.setattr(publish, "push", lambda: None)
    publish.publish("conn")
    return len(calls)


def test_push_once_after_success(monkeypatch):
    pushes = []
    monkeypatch.setattr(publish, "publish_once", lambda conn: None)
    monkeypatch.setattr(publish, "push", lambda: pushes.append(1))
    publish.publish("conn")
    assert pushes == [1]


def test_push_only_warns(monkeypatch, capsys):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    publish.push()

    def down(*args, **kwargs):
        raise urllib.error.URLError("down")

    monkeypatch.setenv("CRON_SECRET", "token")
    monkeypatch.setattr(publish.urllib.request, "urlopen", down)
    publish.push()
    out = capsys.readouterr().out
    assert "WARNING publish: no CRON_SECRET" in out and "WARNING publish: the Telegram push failed" in out and "token" not in out


def test_retries_deadlock_and_serialization(monkeypatch, capsys):
    assert run(monkeypatch, [psycopg.errors.DeadlockDetected("x"), psycopg.errors.SerializationFailure("y")]) == 3
    assert capsys.readouterr().out.count("WARNING publish: Neon rolled back") == 2


def test_gives_up_after_retries(monkeypatch):
    with pytest.raises(psycopg.errors.DeadlockDetected):
        run(monkeypatch, [psycopg.errors.DeadlockDetected("x")] * (publish.RETRIES + 1))


def test_other_errors_not_retried(monkeypatch):
    with pytest.raises(RuntimeError):
        run(monkeypatch, [RuntimeError("no url")])


def test_clean_url_naukrigulf_unwraps_to_job_page():
    from urllib.parse import quote
    job = "https://www.naukrigulf.com/data-engineer-jobs-in-cairo-n-cd-1-jid-2?utm_source=mail&mailerId=9"
    login = "https://www.naukrigulf.com/nglogin/user/mailerLogin?conmailer=SECRET&rUrl=" + quote(job, safe="")
    url = "https://ccs-tracking.naukrigulf.com/?data=SECRET&redirect=" + quote(login, safe="")
    assert publish.clean_url(url) == "https://www.naukrigulf.com/data-engineer-jobs-in-cairo-n-cd-1-jid-2"
    assert publish.clean_url("https://ccs-tracking.naukrigulf.com/?data=SECRET") is None


def test_clean_url_indeed_linkedin_and_opaque():
    assert publish.clean_url("https://eg.indeed.com/rc/clk/dl?jk=abc123&tk=SECRET&alid=SECRET&from=ja") == "https://eg.indeed.com/viewjob?jk=abc123"
    assert publish.clean_url("https://www.linkedin.com/jobs/view/42?trk=x&refId=y&trackingId=z&midToken=SECRET") == "https://www.linkedin.com/jobs/view/42"
    assert publish.clean_url("https://boards.greenhouse.io/x/jobs/1?gh_jid=1&utm_source=mail") == "https://boards.greenhouse.io/x/jobs/1?gh_jid=1"
    assert publish.clean_url("https://links.wellfound.com/s/c/OPAQUE/25") is None


# ---- publish_once against a fake warehouse and a fake Neon: the statements it sends ----
from datetime import datetime, timezone  # noqa: E402

COLS = publish.JOB + publish.WIDE


class Rows(list):
    """A result: iterable, with fetchall and fetchone."""

    def fetchall(self):
        return list(self)

    def fetchone(self):
        return self[0] if self else None


class Warehouse:
    """core.* as publish_once reads it: `jobs` (job_id -> job_key, all source 'greenhouse') and `pairs` (job_id, skill)."""

    def __init__(self, jobs: dict, pairs: list):
        self.jobs, self.pairs = jobs, pairs

    def execute(self, sql, params=None):
        if "core.job_archive" in sql:
            return Rows()
        if sql.startswith("SELECT job_id, job_key FROM core.job"):
            return Rows(self.jobs.items())
        if "core.job_skill" in sql:
            return Rows(self.pairs)
        if "core.skill_day" in sql:
            return Rows([(datetime.now(timezone.utc).date(), "Python", 1, 1)])
        if "core.startup" in sql or "core.claude_grade" in sql:
            return Rows()
        if "GROUP BY source" in sql:
            return Rows([("greenhouse", len(self.jobs), True, None, datetime.now(timezone.utc))])
        if "FROM core.skill" in sql:
            return Rows([("Python", "Data & Analytics", "python")])
        row = dict.fromkeys(COLS)
        return Rows(tuple({**row, "job_id": i, "source": "greenhouse", "job_url": f"https://x/{i}"}[c] for c in COLS) + (key,)
                    for i, key in self.jobs.items())


class Neon:
    """A Neon connection and its cursor: every table there, an owner, nothing to remap; `sent` is every (sql, params)."""

    def __init__(self):
        self.sent, self.rowcount, self.rows = [], 0, Rows()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def cursor(self):
        return self

    def __iter__(self):
        return iter(self.rows)

    def execute(self, sql, params=None):
        self.sent.append((sql, params))
        self.rows = Rows([(c,) for c in COLS + ("job_key",)] if "information_schema.columns" in sql
                         else [(0,)] if "count(*)" in sql else [(True,)] if "to_regclass" in sql and "unnest" not in sql
                         else [(1,)] if "app.app_user" in sql else [("job_status",)] if "AS t WHERE to_regclass" in sql else [])
        return self

    def executemany(self, sql, rows):
        self.sent.append((sql, rows))

    def fetchone(self):
        return self.rows.fetchone()

    def fetchall(self):
        return self.rows.fetchall()


def publish_once(monkeypatch, pairs, github=False) -> Neon:
    """publish_once on two jobs (1 and 2) and `pairs`; returns the fake Neon with what was sent to it."""
    neon = Neon()
    monkeypatch.setenv("NEON_DATABASE_URL", "postgresql://fake")  # the fake below is all it reaches
    monkeypatch.delenv("PUBLISH_PRUNE", raising=False)
    if github:
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
    else:
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(publish.psycopg, "connect", lambda *a, **kw: neon)
    publish.publish_once(Warehouse({1: "key one", 2: "key two"}, pairs))
    return neon


def sent(neon: Neon, start: str) -> list:
    return [(sql, params) for sql, params in neon.sent if sql.startswith(start)]


def test_the_jobs_prune_deletes_by_age_not_what_this_warehouse_holds(monkeypatch):
    """The laptop never seeds from Neon: a job the GitHub run added is not in its core.job and must stay until it ages out."""
    (sql, params), = sent(publish_once(monkeypatch, [(1, "Python")]), "DELETE FROM jobs.job j")
    assert "j.source <> 'email' AND coalesce(j.posted_at::date, j.first_seen) < current_date - %s" in sql
    # only a Gmail alert job (always sent while the warehouse holds it) is deleted for no longer being sent
    assert "j.source = 'email' AND j.job_id <> ALL(%s)" in sql and params[1:] == (publish.KEEP_DAYS,)
    assert "j.source NOT LIKE 'email:%%'" in sql  # the site's own email jobs stay
    assert "app.job_status" in sql and "tracker.application_email" in sql  # a job a user tracks stays, whatever its age


def test_the_job_skills_prune_touches_only_the_jobs_sent_with_pairs(monkeypatch, capsys):
    """Job 2 has no pair here (a seeded job older than match_skills' window): its pairs on Neon are not deleted."""
    (sql, params), = sent(publish_once(monkeypatch, [(1, "Python")]), "DELETE FROM jobs.job_skill")
    assert "t.job_id = ANY(%s)" in sql and params[0] == [publish.nat_id("key one")]
    # no pair at all: the jobs still go, nothing of jobs.job_skill is deleted, and the run log says so
    neon = publish_once(monkeypatch, [])
    (sql, params), = sent(neon, "DELETE FROM jobs.job_skill")
    assert params[0] == [] and sent(neon, "INSERT INTO jobs.job ")
    assert "WARNING publish: core.job_skill has 0 rows for the published jobs" in capsys.readouterr().out


def test_the_github_run_leaves_source_counts_and_skill_days_to_the_laptop(monkeypatch, capsys):
    neon = publish_once(monkeypatch, [(1, "Python")], github=True)
    assert not sent(neon, "INSERT INTO jobs.source_count") and not sent(neon, "INSERT INTO jobs.skill_day")
    assert not sent(neon, "DELETE FROM jobs.source_count")
    assert "publish: on GitHub Actions: jobs.source_count and jobs.skill_day left as they are" in capsys.readouterr().out
    neon = publish_once(monkeypatch, [(1, "Python")])  # the laptop writes them, and prunes the counts
    assert sent(neon, "INSERT INTO jobs.source_count") and sent(neon, "INSERT INTO jobs.skill_day")
    assert sent(neon, "DELETE FROM jobs.source_count")
