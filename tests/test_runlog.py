r"""The run log's accounting, without the network or the warehouse. Run on the laptop:

    .venv\Scripts\python -m pytest tests/test_runlog.py
"""

from contextlib import nullcontext
from decimal import Decimal
from types import SimpleNamespace

import anthropic
import pytest
from anthropic.resources.messages import Messages

from job_radar import runlog


def usage(i=0, o=0, w=0, r=0):
    return SimpleNamespace(input_tokens=i, output_tokens=o, cache_creation_input_tokens=w, cache_read_input_tokens=r)


@pytest.fixture
def rows(monkeypatch):
    """The SDK wrapped as the real run does, with the HTTP post replaced; run() writes into the list."""
    create, parse = Messages.create, Messages.parse
    monkeypatch.setattr(runlog, "_watching", False)
    monkeypatch.setattr(anthropic.Anthropic, "post", lambda self, *a, **k: SimpleNamespace(
        model="claude-sonnet-5-5-20261001", usage=usage(1000, 200, 3000, 4000)))  # what Messages._post calls
    out: list[dict] = []
    monkeypatch.setattr(runlog, "save", out.append)
    monkeypatch.setattr(runlog, "connect", lambda: nullcontext())  # run() closes stale rows of its step: not here
    monkeypatch.setattr(runlog, "close_stale", lambda log, step: 0)
    runlog.watch_claude()
    yield out
    Messages.create, Messages.parse = create, parse


def ended(rows):
    """The rows a run wrote when its steps ended (each step also writes a 'running' row first)."""
    return [r for r in rows if r["status"] != "running"]


def ask(method):
    client = anthropic.Anthropic(api_key="test")
    return getattr(client.messages, method)(model="claude-sonnet-5-5", max_tokens=1,
                                            messages=[{"role": "user", "content": "x"}])


def test_price_math_is_exact():
    # 1M of each token kind on Sonnet: 2 + 10 + 2.50 + 0.20
    assert runlog.cost({"claude-sonnet-5-5": [10**6] * 4}) == Decimal("14.70")
    # Haiku, a dated id prices as its prefix: 0.10 + 0.50 + 0.125 + 0.01
    assert runlog.cost({"claude-haiku-5-5-20261001": [10**6] * 4}) == Decimal("0.735")
    # one call: 1000 in, 200 out, 3000 cache write, 4000 cache read
    assert runlog.cost({"claude-sonnet-5-5": [1000, 200, 3000, 4000]}) == Decimal("0.0123")


def test_cost_is_empty_without_a_call_or_a_price(capsys):
    assert runlog.cost({}) is None
    assert runlog.cost({"some-other-model": [1, 1, 1, 1]}) is None
    assert "no price for some-other-model" in capsys.readouterr().out


@pytest.mark.parametrize("method", ["create", "parse"])
def test_one_call_is_counted_once(rows, method):  # parse posts itself, so it must not also count through create
    def step(conn):
        ask(method)
        print("step: done")
    runlog.run(None, "step", step)
    (row,) = ended(rows)
    assert (row["input"], row["output"], row["write"], row["read"]) == (1000, 200, 3000, 4000)
    assert row["model"] == "claude-sonnet-5-5-20261001"
    assert row["cost"] == Decimal("0.0123")


def test_watching_twice_does_not_count_twice(rows):
    runlog.watch_claude()
    runlog.run(None, "step", lambda conn: ask("create"))
    assert ended(rows)[0]["input"] == 1000


def test_threads_add_up(rows):
    from concurrent.futures import ThreadPoolExecutor

    def step(conn):
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda _: ask("parse"), range(40)))
    runlog.run(None, "grade_claude", step)
    assert (ended(rows)[0]["input"], ended(rows)[0]["output"]) == (40_000, 8_000)


def test_each_step_counts_only_its_own_calls(rows):
    runlog.run(None, "grade_claude", lambda conn: ask("create"))
    runlog.run(None, "describe", lambda conn: print("describe: 3 jobs"))
    assert [r["input"] for r in ended(rows)] == [1000, 0]
    assert ended(rows)[1]["model"] is None and ended(rows)[1]["cost"] is None  # no model call: no cost


def test_summary_is_the_last_non_blank_line_and_the_header_is_not_in_it(rows):
    runlog.run(None, "embed", lambda conn: print("embed: 5 jobs\nembed tokens: 0\n\n   \n", end=""))
    assert ended(rows)[0]["summary"] == "embed tokens: 0" and ended(rows)[0]["status"] == "ok"


def test_off_is_the_steps_own_words_only(rows):
    runlog.run(None, "grade_claude", lambda conn: print("grade_claude: off in settings.yaml (features.grade)"))
    runlog.run(None, "match_skills", lambda conn: print("match_skills: 4 matched, restrictions off in settings.yaml"))
    assert [r["status"] for r in ended(rows)] == ["off", "ok"]


def test_a_failed_step_is_recorded_then_raised_again(rows):
    def step(conn):
        print("transform: started")
        raise RuntimeError("the warehouse went away")
    with pytest.raises(RuntimeError, match="warehouse went away"):
        runlog.run(None, "transform", step)
    (row,) = ended(rows)
    assert row["status"] == "failed" and row["summary"] == "RuntimeError: the warehouse went away"
    assert row["finished_at"] >= row["started_at"]


def test_a_step_leaves_a_running_row_when_it_starts_and_the_same_row_is_updated_when_it_ends(rows):
    seen_while_running = []
    runlog.run(None, "embed", lambda conn: seen_while_running.append(list(rows)))
    ((during,),) = seen_while_running  # the one row there while the step ran
    assert during["status"] == "running" and during["finished_at"] is None and during["summary"] is None
    start, end = rows  # a step killed by Airflow never reaches its end: its row stays as it was above
    assert (start["step"], start["started_at"]) == (end["step"], end["started_at"])  # one row per step and start: the end updates it
    assert end["status"] == "ok" and end["finished_at"] is not None


def test_the_summary_keeps_every_line_that_shows_trouble_not_only_the_last(rows):
    def step(conn):
        print("describe: starting")
        print("WARNING dailyremote: no cards")
        print("grade_claude: 0 of 12 jobs graded")
        print("embed: skipped, no model")
        print("describe: 10 of 20 fine")  # '10 of' is not '0 of'
        print("describe: 5 descriptions found")
    runlog.run(None, "describe", step)
    (row,) = ended(rows)
    assert row["summary"] == ("WARNING dailyremote: no cards | grade_claude: 0 of 12 jobs graded | "
                              "embed: skipped, no model | describe: 5 descriptions found")
    assert row["status"] == "warn"  # a WARNING line, and only that, makes a step warn


def test_skipped_and_zero_of_keep_their_line_but_stay_ok(rows):
    runlog.run(None, "tailor", lambda conn: print("tailor: no CV uploaded in the tracker, skipped"))
    (row,) = ended(rows)
    assert row["summary"] == "tailor: no CV uploaded in the tracker, skipped" and row["status"] == "ok"


def test_the_summary_is_capped(rows):
    def step(conn):
        for i in range(30):
            print(f"WARNING source{i}: " + "x" * 400)
        print("done")
    runlog.run(None, "extract_remote", step)
    summary = ended(rows)[0]["summary"]
    assert summary.count("WARNING") == runlog.SUMMARY_LINES and summary.endswith("| done")  # the last line always stays
    assert len(summary) < 1120


def test_transforms_whole_counts_line_is_kept():
    n = 12345  # five-digit counts: the longest this line gets
    line = (f"transform: {n} postings -> {n} jobs in scope, {n} no longer in scope removed, {n} first seen over 4 days ago "
            f"deleted; postings left out: {n} no role, {n} too senior, {n} not in reach, {n} restricted")
    assert 120 < len(line) <= runlog.LINE_CHARS  # the old cut of 120 characters ended it at "postin"
    tee = runlog.Tee(None)
    tee.last = line
    assert tee.summary() == line
