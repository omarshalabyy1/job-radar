"""Today: what to apply to next, where your applications stand, and whether the last run worked."""

import altair as alt
import streamlit as st

from job_radar.config import FEATURES
from job_radar.db import connect
from tracker.applications_tab import follow_ups
from tracker.common import BLUE, PRIORITY_ROLES, colors, filtered, load_jobs, nothing_matches, numbers, query
from tracker.runlog_tab import dollars

st.title("Today", icon=":material/today:")
jobs = load_jobs()
shown = filtered(jobs)
numbers(jobs, shown)
ink = colors()["ink"]

# 1. what to do now: data engineering and AI jobs first (roles 1-3 in settings.yaml), the freshest and best graded on top
st.subheader("Apply next")
st.caption("Posted in the last 48 hours: data engineering and AI roles first, then the best graded.")
best = (shown[shown["fresh_level"] <= 3]
        .assign(priority=lambda d: d["role_rank"] > PRIORITY_ROLES, unnamed=lambda d: d["company"] == "")
        .sort_values(["priority", "unnamed", "score", "skill_matches"], ascending=[True, True, False, False], na_position="last")
        .head(8))
if shown.empty:
    nothing_matches()
elif best.empty:
    st.info("Nothing posted in the last 48 hours matches these filters. The Jobs page has the older ones.", icon=":material/schedule:")
else:
    st.dataframe(best[[c for c in ["score", "title", "company", "place", "posted", "why", "job_url"]
                       if FEATURES["grade"] or c not in ("score", "why")]], hide_index=True, column_config={
        "score": st.column_config.ProgressColumn("Score", min_value=0, max_value=10, format="%d", width="small",
                                                 help="Claude's grade against your CV, 0-10; empty when not graded"),
        "title": st.column_config.TextColumn("Title", width=300), "company": st.column_config.TextColumn("Company", width=170),
        "place": st.column_config.TextColumn("Where", width="small"), "posted": st.column_config.TextColumn("Posted", width=110),
        "why": st.column_config.TextColumn("Why", width="large", help="Claude's reason for the score"),
        "job_url": st.column_config.LinkColumn("Link", display_text="Open", width="small")})

# 2. your applications: the stages from your mailbox, and what is due today
st.subheader("Your applications")
stage = query("SELECT kind, count(DISTINCT lower(coalesce(company, title))) AS companies FROM core.application_email"
              " WHERE kind IN ('applied', 'viewed', 'interview', 'offer', 'hired') GROUP BY kind")
funnel = (stage.set_index("kind")["companies"].reindex(["applied", "viewed", "interview", "offer", "hired"], fill_value=0)
          .rename(lambda k: k.capitalize()).rename_axis("Stage").reset_index(name="Companies"))
with connect() as conn:
    due = len(follow_ups(conn))
left, right = st.columns([2, 1], gap="large")
stages = alt.Chart(funnel).encode(
    y=alt.Y("Stage:N", sort=list(funnel["Stage"]), title=None, axis=alt.Axis(ticks=False, domain=False)),
    x=alt.X("Companies:Q", title=None, axis=None, scale=alt.Scale(domain=[0, max(funnel["Companies"].max(), 1) * 1.15])),
    tooltip=[alt.Tooltip("Stage:N"), alt.Tooltip("Companies:Q")])
left.altair_chart((stages.mark_bar(color=BLUE, cornerRadiusEnd=4, height=22)
                   + stages.mark_text(align="left", dx=4, color=ink).encode(text="Companies:Q"))
                  .properties(title="Companies by stage, from your mailbox", height=36 * len(funnel)), width="stretch")
applied = int(funnel.loc[funnel["Stage"] == "Applied", "Companies"].iloc[0])
if applied:
    left.caption(" · ".join(f"{r.Stage} {r.Companies / applied:.0%}" for r in funnel.itertuples() if r.Stage != "Applied")
                 + " of the companies you applied to")
right.metric("Applied in the tracker", int((jobs["status"] == "applied").sum()), border=True,
             help="Jobs you set to applied, with or without a confirmation email")
right.metric("Follow-ups due today", due, border=True, help="Applied 7 or 14 days ago with no answer yet (Applications)")

# 3. the pipeline: did the last run work, and what it cost
st.subheader("The pipeline")
runs = query("SELECT DISTINCT ON (step) step, status, started_at FROM core.step_run ORDER BY step, started_at DESC")
if runs.empty:
    st.caption("No run logged yet: the next run fills this.")
else:
    month = query("SELECT sum(cost_usd) AS cost_usd FROM core.step_run"
                  " WHERE started_at >= date_trunc('month', now() AT TIME ZONE 'Africa/Cairo')")
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
