"""Today: what to apply to next, your deadlines, and whether the last run worked. The Overview counts your applications."""

import pandas as pd
import streamlit as st

from job_radar.config import FEATURES
from job_radar.db import connect
from tracker.common import default_order, filtered, load_jobs, nothing_matches, numbers, query, save_edits, table_key
from tracker.decide import apply_decisions, mark_deadlines_done
from tracker.runlog_tab import CAIRO, dollars

st.title("Today", icon=":material/today:")
jobs = load_jobs()
shown = filtered(jobs)
numbers(jobs, shown)

# 1. what to do now: data engineering and AI jobs first (roles 1-3 in settings.yaml), the freshest and best graded on top
st.subheader("Apply next")
st.caption("Posted in the last 48 hours: Egypt, then remote, then country by country; data and BI before AI; entry level to senior.")
best = default_order(shown[shown["fresh_level"] <= 3].assign(unnamed=lambda d: d["company"] == "")).sort_values("unnamed", kind="stable").head(8)
if shown.empty:
    nothing_matches()
elif best.empty:
    st.info("Nothing posted in the last 48 hours matches these filters. The Jobs page has the older ones.", icon=":material/schedule:")
else:
    st.data_editor(best.assign(applied=False, noise=False)[["job_id", *[c for c in ["applied", "noise", "score", "title", "company", "place", "source", "posted", "why", "job_url"]
                                                         if FEATURES["grade"] or c not in ("score", "why")]]],
                   key=table_key("apply-next"), hide_index=True, on_change=save_edits,
                   args=("apply-next", apply_decisions, [int(j) for j in best["job_id"]]),
                   column_order=[c for c in ["applied", "noise", "score", "title", "company", "place", "source", "posted", "why", "job_url"]
                                 if FEATURES["grade"] or c not in ("score", "why")],
                   disabled=["job_id", "score", "title", "company", "place", "source", "posted", "why", "job_url"], column_config={
        "applied": st.column_config.CheckboxColumn("Applied", width="small", help="Tick it when you applied: saved at once, the job leaves this list"),
        "noise": st.column_config.CheckboxColumn("Noise", width="small", help="Tick it when the job is noise (applications closed): saved at once, the job is hidden"),
        "score": st.column_config.ProgressColumn("Score", min_value=0, max_value=10, format="%d", width="small",
                                                 help="Claude's grade against your CV, 0-10; empty when not graded"),
        "title": st.column_config.TextColumn("Title", width=300), "company": st.column_config.TextColumn("Company", width=170),
        "place": st.column_config.TextColumn("Where", width="small"), "source": st.column_config.TextColumn("Source", width="small"), "posted": st.column_config.TextColumn("Posted", width=110),
        "why": st.column_config.TextColumn("Why", width="large", help="Claude's reason for the score"),
        "job_url": st.column_config.LinkColumn("Link", display_text="Open", width="small")})

# 2. your deadlines (core.deadline): the next three not done, and every one to edit
st.subheader("Deadlines")
deadlines = query("SELECT d.deadline_id, d.due, d.what, d.note, d.done, d.job_id,"
                  " nullif(concat_ws(' at ', j.title, nullif(j.company, '')), '') AS job, d.due - (now() AT TIME ZONE 'Africa/Cairo')::date AS days_left"
                  " FROM core.deadline d LEFT JOIN core.job j USING (job_id) ORDER BY d.done, d.due")
deadlines["done"] = deadlines["done"].astype(bool)  # an empty table gives an object column, and ~ of it is no mask
# every overdue one of the last 2 weeks (they are the most urgent; an older one is dead, the editor below keeps it), then the next three
STALE_DAYS = 14
open_ = deadlines[~deadlines["done"]]
upcoming = pd.concat([open_[(open_["days_left"] < 0) & (open_["days_left"] >= -STALE_DAYS)], open_[open_["days_left"] >= 0].head(3)])
if upcoming.empty:
    st.caption("No deadline set: add one below.")
else:
    upcoming = upcoming.assign(when=upcoming["days_left"].map(
        lambda n: f"Overdue {-n} day{'s' if n != -1 else ''}" if n < 0 else "Today" if n == 0 else f"In {n} day{'s' if n != 1 else ''}"))
    st.data_editor(upcoming[["deadline_id", "due", "when", "what", "job", "note", "done"]], key=table_key("deadline-list"), hide_index=True,
                   on_change=save_edits, args=("deadline-list", mark_deadlines_done, [int(i) for i in upcoming["deadline_id"]]),
                   column_order=["due", "when", "what", "job", "note", "done"],
                   disabled=["deadline_id", "due", "when", "what", "job", "note"], column_config={
        "due": st.column_config.DateColumn("Due", format="ddd D MMM", width="small"),
        "when": st.column_config.TextColumn("When", width=130),
        "what": st.column_config.TextColumn("What", width=200), "job": st.column_config.TextColumn("Job", width=260),
        "note": st.column_config.TextColumn("Note", width="large"),
        "done": st.column_config.CheckboxColumn("Done", help="Tick it: saved at once, the deadline leaves this list")})
with st.expander("Edit deadlines"):
    edited = st.data_editor(deadlines, key="deadlines", hide_index=True, num_rows="dynamic",
                            column_order=["due", "what", "note", "job_id", "done"], column_config={
                                "due": st.column_config.DateColumn("Due", required=True),
                                "what": st.column_config.TextColumn("What", required=True),
                                "note": st.column_config.TextColumn("Note", width="large"),
                                "job_id": st.column_config.NumberColumn("Job id", help="The job in the radar, if any", format="%d"),
                                "done": st.column_config.CheckboxColumn("Done", default=False)})
    if st.button("Save deadlines", icon=":material/save:"):
        with connect() as conn:
            # only the rows you deleted here: one added since the page loaded (an Apply by from the daily run) stays
            removed = [int(i) for i in set(deadlines["deadline_id"]) - set(edited["deadline_id"].dropna())]
            if removed:
                conn.execute("DELETE FROM core.deadline WHERE deadline_id = ANY(%s)", (removed,))
            for d in edited.itertuples():
                row = (d.due, d.what, d.note if isinstance(d.note, str) and d.note.strip() else None,
                       int(d.job_id) if pd.notna(d.job_id) else None, bool(d.done))
                if pd.notna(d.deadline_id):  # empty for a new row
                    conn.execute("UPDATE core.deadline SET due = %s, what = %s, note = %s, job_id = %s, done = %s"
                                 " WHERE deadline_id = %s", (*row, int(d.deadline_id)))
                else:
                    conn.execute("INSERT INTO core.deadline (due, what, note, job_id, done) VALUES (%s, %s, %s, %s, %s)", row)
        query.clear()
        st.rerun()

# 3. the pipeline: did the last run work, and what it cost
st.subheader("The pipeline")
runs = query("SELECT DISTINCT ON (step) step, status, started_at FROM core.step_run ORDER BY step, started_at DESC")
if runs.empty:
    st.caption("No run logged yet: the next run fills this.")
else:
    month = query("SELECT sum(cost_usd) AS cost_usd FROM core.step_run"
                  f" WHERE (started_at {CAIRO}) >= date_trunc('month', now() {CAIRO})")
    ran = runs["started_at"].max().tz_convert("Africa/Cairo")
    problems = int(runs["status"].isin(["warn", "failed"]).sum())
    with st.container(horizontal=True):
        st.metric("Last run", ran.strftime("%a %d %b, %I:%M%p").replace(" 0", " "), border=True, width=210)
        st.metric("Steps ok", f"{int((runs['status'] == 'ok').sum())} of {len(runs)}", border=True, width=170,
                  help="The latest run of each step")
        st.metric("Warnings and failures", problems, border=True, width=210, help="The Last run page says which")
        st.metric("Claude this month", dollars(month["cost_usd"].iloc[0]), border=True, width=190)
    if problems:
        st.page_link("app_pages/runlog.py", label="See what went wrong", icon=":material/history:")
