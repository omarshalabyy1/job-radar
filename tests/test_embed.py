r"""The CV-match math of embed, without Ollama or the warehouse. Run on the laptop:

    .venv\Scripts\python -m pytest tests/test_embed.py
"""

import pytest

from job_radar.embed import cosine


def test_cosine():
    assert cosine([1, 0], [2, 0]) == pytest.approx(1)  # same direction, any length
    assert cosine([1, 0], [0, 3]) == pytest.approx(0)
    assert cosine([1, 1], [1, 0]) == pytest.approx(0.7071, abs=1e-4)
    assert cosine([0, 0], [1, 0]) == 0  # an empty vector matches nothing, no division by zero


# ---- which CVs are embedded again, against the warehouse (rolled back; skipped without it) ----
from pathlib import Path  # noqa: E402

import psycopg  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from job_radar import db, embed  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


@pytest.fixture
def conn():
    try:
        connection = db.connect()
    except (KeyError, psycopg.OperationalError) as e:
        pytest.skip(f"no warehouse: {e!r}"[:120])
    yield connection
    connection.rollback()  # nothing the test wrote stays
    connection.close()


def test_a_cv_is_embedded_again_only_when_it_is_new_changed_or_from_another_model(conn):
    stale = lambda: {label for label, _ in embed.stale_cvs(conn, "m1") if label == "zz embed test"}  # noqa: E731
    conn.execute("INSERT INTO core.cv (label, filename, text, uploaded_at) VALUES ('zz embed test', 'zz.pdf', 'sql', now())")
    assert stale() == {"zz embed test"}  # no vector yet
    conn.execute("INSERT INTO core.cv_vector (label, vector, model, embedded_at) VALUES ('zz embed test', '{1,0}', 'm1', now())")
    assert stale() == set()  # vector made at or after the upload: left alone
    conn.execute("UPDATE core.cv SET uploaded_at = now() + interval '1 hour' WHERE label = 'zz embed test'")
    assert stale() == {"zz embed test"}  # uploaded again after its vector
    conn.execute("UPDATE core.cv SET uploaded_at = now() - interval '1 hour' WHERE label = 'zz embed test'")
    assert stale() == set()
    assert {label for label, _ in embed.stale_cvs(conn, "m2") if label == "zz embed test"} == {"zz embed test"}  # another model
    conn.execute("UPDATE core.cv_vector SET embedded_at = NULL WHERE label = 'zz embed test'")
    assert stale() == {"zz embed test"}  # a vector made before the column existed


def test_embed_stops_at_its_switch_before_touching_the_warehouse_or_ollama(monkeypatch, capsys):
    class Untouched:
        def __getattr__(self, name):
            raise AssertionError(f"the warehouse was used: {name}")

    monkeypatch.setitem(embed.FEATURES, "embeddings", False)
    monkeypatch.setattr(embed, "vectors_of", lambda texts: pytest.fail("Ollama was called"))
    embed.embed(Untouched())
    assert capsys.readouterr().out.strip() == "embed: off in settings.yaml (features.embeddings)"
