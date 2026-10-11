r"""The collect DAG's shape, without Airflow (its file imports Airflow, so its source is read, not run). Run on the laptop:
.venv\Scripts\python -m pytest tests/test_dag.py
"""

import ast
from pathlib import Path

DAG = Path(__file__).resolve().parent.parent / "dags" / "job_radar.py"


def test_a_run_is_red_when_schema_or_an_extract_did_not_succeed():
    """check_extracts is an empty task behind every extract with Airflow's default rule (all_success): schema failing sends its
    extracts to upstream_failed (the run once showed success on what was there) and a failed extract does the same to it, and a
    leaf that is upstream_failed turns the run red. So it must stay a leaf below the extracts and must not set trigger_rule."""
    tree = ast.parse(DAG.read_text(encoding="utf-8"))
    checks = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "EmptyOperator"
              and any(k.arg == "task_id" and getattr(k.value, "value", None) == "check_extracts" for k in n.keywords)]
    assert len(checks) == 1 and [k.arg for k in checks[0].keywords] == ["task_id"]  # no trigger_rule, no outlets
    below = [n for n in ast.walk(tree) if isinstance(n, ast.BinOp) and isinstance(n.op, ast.RShift) and n.right is checks[0]]
    assert len(below) == 1 and below[0].left.id == "extracts"  # downstream of every extract
    assert not any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.RShift) and n.left is checks[0] for n in ast.walk(tree))  # a leaf


def test_the_emails_start_from_a_task_that_succeeds_even_when_grading_fails():
    """Airflow emits an Asset event only when the task with the outlet succeeds. On grade_claude a red grading meant
    no email at all, so the outlet sits on `ranked`: an all_done task right after grade_claude, before tailor."""
    source = DAG.read_text(encoding="utf-8")
    assert source.count("outlets=") == 1  # one carrier of the Asset, and it is not grade_claude
    assert 'ranked = BashOperator(task_id="ranked", trigger_rule="all_done", outlets=[COLLECTED],' in source
    assert 'graded = step("grade_claude", trigger_rule="all_done", execution_timeout=timedelta(seconds=600))' in source
    assert 'graded >> ranked >> step("tailor", trigger_rule="all_done"' in source  # emails do not wait for tailor
    assert source.count("schedule=[COLLECTED]") == 1  # both email DAGs hang off the one Asset


def test_a_trigger_sends_no_email_when_manual_collect_emails_is_off():
    """With features.manual_collect_emails false, `ranked` exits 99 (BashOperator's skip code) on a run that is not the
    schedule's: a skipped task emits no Asset event, so no email DAG starts, and tailor (all_done) still runs."""
    source = DAG.read_text(encoding="utf-8")
    assert """bash_command="true" if SETTINGS["features"]["manual_collect_emails"]""" in source
    assert """else '[[ "{{ run_id }}" == scheduled__* ]] || exit 99')""" in source
    assert 'matched >> checked >> embedded >> graded >> ranked >> step("tailor", trigger_rule="all_done"' in source


def test_the_dag_retries_schema_and_puts_every_extract_behind_it():
    source = DAG.read_text(encoding="utf-8")
    assert 'step("schema", retries=2, retry_delay=timedelta(seconds=60)) >> extracts' in source
