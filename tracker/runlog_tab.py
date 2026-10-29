"""The tracker's Last run tab: how each step last went (core.step_run, written by runlog.py), and what
Claude cost and which steps failed over the last 14 days. Days are Cairo days. A step still 'running' after its
Airflow timeout (dags/job_radar.py) was killed, and shows as killed?."""

import streamlit as st

STATUS = {"ok": "ok", "warn": "WARN", "failed": "FAILED", "off": "off", "running": "running"}
CAIRO = "AT TIME ZONE 'Africa/Cairo'"
# seconds Airflow lets a step run (execution_timeout in dags/job_radar.py; the others get 240), copied by hand: the DAG file
# imports Airflow, which the tracker does not have. Change both together.
TIMEOUTS = {"extract_linkedin": 300, "describe": 150, "embed": 240, "grade_claude": 600, "tailor": 300,
            "track_applications": 300}


def dollars(amount) -> str:
    """Dollars to the cent; n/a when no cost was stored (not $0)."""
    return "n/a" if amount is None or amount != amount else f"${amount:.2f}"


def badge(step: str, status: str, age: int) -> str:
    """The status as shown: a step that has run longer than its timeout is killed?, not running."""
    return "killed?" if status == "running" and age > TIMEOUTS.get(step, 240) else STATUS[status]


def render(query) -> None:
    last = query("SELECT DISTINCT ON (step) step, status, summary, started_at,"
                 " extract(epoch FROM finished_at - started_at)::int AS seconds, extract(epoch FROM now() - started_at)::int AS age,"
                 " input_tokens + output_tokens + cache_write_tokens + cache_read_tokens AS tokens, cost_usd"
                 " FROM core.step_run ORDER BY step, started_at DESC")
    if last.empty:
        st.caption("No run logged yet: the next run of python -m job_radar fills this tab.")
        return
    last = last.sort_values("started_at", ascending=False)  # the latest run first
    last["status"] = [badge(*row) for row in zip(last["step"], last["status"], last.pop("age"))]
    last["cost_usd"] = last["cost_usd"].map(dollars)
    st.dataframe(last, hide_index=True, column_config={
        "step": "Step", "status": "Status", "summary": st.column_config.TextColumn("What it said", width="large"),
        "started_at": st.column_config.DatetimeColumn("Started", format="D MMM, HH:mm"),
        "seconds": st.column_config.NumberColumn("Took", format="%d s"),
        "tokens": st.column_config.NumberColumn("Tokens", format="%d"),
        "cost_usd": "Claude $"})
    days = query(f"SELECT (started_at {CAIRO})::date AS day, sum(cost_usd) AS cost_usd,"
                 " count(*) FILTER (WHERE status = 'failed') AS failed FROM core.step_run"
                 f" WHERE (started_at {CAIRO})::date >= (now() {CAIRO})::date - 13 GROUP BY 1 ORDER BY 1 DESC")
    days["cost_usd"] = days["cost_usd"].map(dollars)
    st.dataframe(days, hide_index=True, column_config={
        "day": st.column_config.DateColumn("Day", format="ddd D MMM"), "cost_usd": "Claude $",
        "failed": st.column_config.NumberColumn("Failed steps", format="%d")})
    month = query(f"SELECT sum(cost_usd) AS cost_usd FROM core.step_run"
                  f" WHERE (started_at {CAIRO}) >= date_trunc('month', now() {CAIRO})")
    st.caption(f"Claude this month: {dollars(month['cost_usd'].iloc[0])}, at the prices in job_radar/runlog.py.")
