"""seed: what it reads out of Neon (the free plan counts every byte read out)."""

from job_radar import seed


class FakeNeon:
    def __init__(self):
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sent.append(sql)
        return self

    def fetchall(self):
        return []

    def __iter__(self):
        return iter([])

    def cursor(self):  # the warehouse side: the inserts of no rows
        return self

    def executemany(self, sql, rows):
        self.sent.append(sql)

    def fetchone(self):
        return (0,)


def run_seed(monkeypatch, light: bool) -> str:
    neon = FakeNeon()
    monkeypatch.setenv("NEON_DATABASE_URL", "postgresql://user:pw@neon.example/db")
    monkeypatch.setenv("SEED_DESCRIPTIONS", "0" if light else "1")
    monkeypatch.delenv("MAILBOXES", raising=False)
    monkeypatch.setattr(seed.psycopg, "connect", lambda *a, **k: neon)
    monkeypatch.setattr(seed, "connect", lambda: FakeNeon())  # the warehouse: nothing to insert for no rows
    seed.main()
    return next(sql for sql in neon.sent if "FROM jobs.job ORDER BY job_key" in sql)


def test_the_applications_seed_reads_no_descriptions_and_the_full_seed_does(monkeypatch):
    assert "NULL AS description" in run_seed(monkeypatch, light=True)
    full = run_seed(monkeypatch, light=False)
    assert "NULL AS description" not in full and ", description" in full
