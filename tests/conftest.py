"""The tests never touch the live warehouse database: every connection goes to a throwaway database on the same server
(jobradar_test_<pid>), made from sql/schema.sql when the run starts and dropped when it ends. Without a warehouse the
database tests skip themselves, as before."""

import os
import time
from pathlib import Path

import psycopg
import pytest

from job_radar import config, db  # noqa: F401  (config loads .env: the warehouse password)
from job_radar.sources import base

SCHEMA = (Path(__file__).resolve().parent.parent / "sql" / "schema.sql").read_text(encoding="utf-8")
TEST_DB = f"jobradar_test_{os.getpid()}"


@pytest.fixture(scope="session", autouse=True)
def test_database():
    os.environ["WAREHOUSE_DB"] = "jobradar"
    try:
        admin = db.connect()
    except (KeyError, psycopg.OperationalError):
        admin = None  # no warehouse: every database test skips
    os.environ["WAREHOUSE_DB"] = TEST_DB  # set before anything connects, so nothing reaches the live database
    if admin is None:
        yield
        return
    admin.autocommit = True
    for (left,) in admin.execute("SELECT datname FROM pg_database WHERE datname LIKE 'jobradar\\_test\\_%'").fetchall():
        admin.execute(f'DROP DATABASE IF EXISTS "{left}" WITH (FORCE)')  # a crashed run's (one run at a time)
    admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    with db.connect() as conn:
        conn.execute(SCHEMA)
    yield
    admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
    admin.close()


@pytest.fixture(autouse=True)
def fresh_time_budget(monkeypatch):
    """Each test starts with a whole step's time budget: the sources stop when time_left(EXTRACT_SECONDS) runs low, and
    that clock counts from the process start, so a test late in a long run would otherwise read nothing."""
    monkeypatch.setattr(base, "STARTED", time.monotonic())


@pytest.fixture(autouse=True)
def no_free_model_keys(monkeypatch):
    """.env holds the free models' keys: no test calls them unless it sets a key itself."""
    for key in ("GROQ_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
