"""job_radar/apply/answers.py: a form question gets an answer only from the approved entries, never from a guess."""
import psycopg
import pytest

from job_radar import db
from job_radar.apply import answers

ENTRIES = [
    {"match": ["notice period", "when can you start"], "answer": "Two weeks"},
    {"match": ["years of experience with python"], "answer": "2"},
    {"match": ["willing to relocate"], "answer": "No"},
    {"match": ["authorized to work", "work authorization"], "answer": "Yes"},
]


@pytest.fixture
def conn():
    try:
        connection = db.connect()
    except (KeyError, psycopg.OperationalError) as e:
        pytest.skip(f"no warehouse: {e!r}"[:120])
    yield connection
    connection.rollback()
    connection.close()


def test_a_known_question_gets_its_approved_answer_whatever_its_wording_case_or_punctuation():
    assert answers.resolve("What is your NOTICE PERIOD?", entries=ENTRIES) == "Two weeks"
    assert answers.resolve("When can you start?", entries=ENTRIES) == "Two weeks"
    assert answers.resolve("Are you authorized to work in Egypt?", entries=ENTRIES) == "Yes"


def test_an_unknown_question_gets_none_not_a_guess():
    assert answers.resolve("What is your favourite colour?", entries=ENTRIES) is None
    assert answers.resolve("Expected salary?", entries=ENTRIES) is None  # no approved entry: parked for Omar


def test_a_select_field_gets_one_of_its_options_or_none():
    assert answers.resolve("Willing to relocate?", "select", ["Yes", "No", "Maybe"], entries=ENTRIES) == "No"
    assert answers.resolve("Willing to relocate?", "select", ["Yes", "Maybe"], entries=ENTRIES) is None  # approved answer not offered
    assert answers.resolve("Notice period", "select", ["2 weeks", "Two weeks (14 days)"], entries=ENTRIES) is None  # no loose matching


def test_a_number_field_only_takes_a_number():
    assert answers.resolve("Years of experience with Python", "number", entries=ENTRIES) == "2"
    assert answers.resolve("Notice period", "number", entries=ENTRIES) is None


def test_no_answer_is_ever_invented():
    approved = {e["answer"] for e in ENTRIES}
    questions = ["salary", "gender", "visa", "why us", "notice period", "relocate", "python years", "linkedin url", "", "  ", "???"]
    for q in questions:
        for ftype, options in (("text", None), ("select", ["Yes", "No"]), ("number", None)):
            got = answers.resolve(q, ftype, options, entries=ENTRIES)
            assert got is None or got in approved


def test_a_missing_or_empty_answers_file_answers_nothing(tmp_path):
    assert answers.load(tmp_path / "nope.yaml") == []
    (tmp_path / "empty.yaml").write_text("", encoding="utf-8")
    assert answers.load(tmp_path / "empty.yaml") == []


def test_the_file_loads_entries(tmp_path):
    f = tmp_path / "a.yaml"
    f.write_text("- match: [notice period]\n  answer: Two weeks\n", encoding="utf-8")
    assert answers.load(f) == [{"match": ["notice period"], "answer": "Two weeks"}]


def test_a_parked_question_is_stored_once_per_job(conn):
    job_id = conn.execute(
        "INSERT INTO core.job (job_key, title, company, location, place, source, job_url, role_rank, role, target_company, first_seen)"
        " VALUES ('zz-park', 't', 'c', 'l', 'Egypt', 's', 'u', 1, 'r', false, current_date) RETURNING job_id").fetchone()[0]
    answers.park(conn, job_id, "Expected salary?")
    answers.park(conn, job_id, "Expected salary?")
    assert conn.execute("SELECT count(*) FROM core.apply_question WHERE job_id = %s", (job_id,)).fetchone()[0] == 1
