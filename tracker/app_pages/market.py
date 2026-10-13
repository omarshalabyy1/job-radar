"""Market: what the jobs your filters keep say about the market, data engineering and AI first: blue is DE and AI,
gray is everything else."""

import altair as alt
import pandas as pd
import streamlit as st

from tracker.common import BLUE, GRAY, ORANGE, PRIORITY_ROLES, colors, filtered, load_jobs, nothing_matches, query

st.title("Market", icon=":material/monitoring:")
jobs = load_jobs()
shown = filtered(jobs)
if shown.empty:
    nothing_matches()
    st.stop()
c = colors()
prio = shown["role_rank"] <= PRIORITY_ROLES
de_ai = shown[prio]
groups = prio.map({True: "DE and AI", False: "Other roles"})
emphasis = alt.Scale(domain=["DE and AI", "Other roles"], range=[BLUE, GRAY])

today = pd.Timestamp.now(tz="Africa/Cairo").date()
per_day = de_ai.groupby("first_seen").size()
earlier = per_day[per_day.index < today]
new_today = int(per_day.get(today, 0))
coverage = de_ai["cv_coverage"].dropna()
with st.container(horizontal=True):
    st.metric("DE and AI jobs", len(de_ai), border=True, width=170,
              delta=f"{len(de_ai) / len(shown):.0%} of {len(shown)}", delta_color="off",
              help="Jobs in your first three roles in settings.yaml, of the jobs your filters keep")
    st.metric("New today", new_today, border=True, width=170,
              delta=f"{new_today - earlier.mean():+.0f} vs a usual day" if len(earlier) else None,
              help="DE and AI jobs first found today, against the average of the earlier days the radar kept")
    st.metric("Remote", f"{(de_ai['place'] == 'Remote').mean():.0%}" if len(de_ai) else "n/a", border=True, width=170,
              help="Share of the DE and AI jobs open to anywhere, or to Egypt")
    st.metric("Median CV coverage", f"{coverage.median():.0f}%" if len(coverage) else "n/a", border=True, width=200,
              help="Of the skills a DE or AI job asks for, how many your best CV shows; the middle job (jobs with a description)")

left, right = st.columns(2, gap="large")
# the days the radar ran: DE and AI in blue on top of the rest in gray
days = shown.assign(group=groups).groupby(["first_seen", "group"]).size().reset_index(name="jobs")
days["day"] = pd.to_datetime(days["first_seen"])
left.altair_chart(alt.Chart(days, title="New jobs a day").mark_bar(cornerRadiusEnd=3, size=26).encode(
    x=alt.X("day:T", title=None, axis=alt.Axis(format="%a %d", tickCount="day", grid=False)),
    y=alt.Y("jobs:Q", title=None, axis=alt.Axis(gridOpacity=0.15, domain=False, ticks=False)),
    color=alt.Color("group:N", title=None, sort=["DE and AI", "Other roles"], legend=alt.Legend(orient="top"), scale=emphasis),
    order=alt.Order("group:N", sort="ascending"),
    tooltip=[alt.Tooltip("day:T", title="Day", format="%A %d %B"), alt.Tooltip("group:N", title="Roles"),
             alt.Tooltip("jobs:Q", title="Jobs")]).properties(height=240), width="stretch")
# where the jobs are: the places with the most DE and AI jobs first
where = shown.assign(group=groups).groupby(["place", "group"]).size().reset_index(name="jobs")
order = list(where[where["group"] == "DE and AI"].sort_values("jobs", ascending=False)["place"])
order += [p for p in where["place"].unique() if p not in order]
right.altair_chart(alt.Chart(where, title="Where the jobs are").mark_bar(cornerRadiusEnd=3, height=18).encode(
    y=alt.Y("place:N", sort=order, title=None, axis=alt.Axis(labelLimit=200, ticks=False, domain=False)),
    x=alt.X("jobs:Q", title=None, axis=alt.Axis(gridOpacity=0.15, domain=False, ticks=False)),
    color=alt.Color("group:N", title=None, sort=["DE and AI", "Other roles"], legend=alt.Legend(orient="top"), scale=emphasis),
    order=alt.Order("group:N", sort="ascending"),
    tooltip=[alt.Tooltip("place:N", title="Where"), alt.Tooltip("group:N", title="Roles"),
             alt.Tooltip("jobs:Q", title="Jobs")]).properties(height=alt.Step(28)), width="stretch")

if len(de_ai):
    left, right = st.columns(2, gap="large")
    # who is hiring for DE and AI: a star marks your target companies
    hiring = (de_ai[de_ai["company"] != ""].groupby("company").agg(jobs=("job_id", "size"), target=("target_company", "max"))
              .sort_values("jobs", ascending=False).head(10).reset_index())
    hiring["label"] = hiring["company"] + hiring["target"].map({True: " ★", False: ""})
    hire = alt.Chart(hiring).encode(
        y=alt.Y("label:N", sort=list(hiring["label"]), title=None, axis=alt.Axis(labelLimit=240, ticks=False, domain=False)),
        x=alt.X("jobs:Q", title=None, axis=None, scale=alt.Scale(domain=[0, max(hiring["jobs"].max(), 1) * 1.15])),
        tooltip=[alt.Tooltip("company:N", title="Company"), alt.Tooltip("jobs:Q", title="DE and AI jobs")])
    left.altair_chart((hire.mark_bar(color=BLUE, cornerRadiusEnd=4, height=18)
                       + hire.mark_text(align="left", dx=4, color=c["ink"]).encode(text="jobs:Q"))
                      .properties(title="Who is hiring for DE and AI (★ your target companies)", height=28 * len(hiring)),
                      width="stretch")
    # the boards that deliver them: their share of DE and AI jobs and the middle score
    boards = (shown[shown["source"] != ""].assign(de_ai=prio).groupby("source")
              .agg(jobs=("job_id", "size"), de_ai=("de_ai", "sum"), score=("score", "median")).reset_index())
    boards["share"] = 100 * boards["de_ai"] / boards["jobs"]
    right.markdown("**Which boards deliver DE and AI jobs**")
    right.dataframe(boards.sort_values(["de_ai", "jobs"], ascending=False).head(8), hide_index=True, width="stretch", column_config={
        "source": "Board", "jobs": st.column_config.NumberColumn("Jobs", format="%d"),
        "de_ai": st.column_config.NumberColumn("DE and AI", format="%d"),
        "share": st.column_config.ProgressColumn("DE and AI share", min_value=0, max_value=100, format="%d%%"),
        "score": st.column_config.NumberColumn("Median score", format="%.0f",
                                               help="Claude's middle score of the graded jobs; empty when none is graded")})

left, right = st.columns(2, gap="large")
if len(coverage):
    # how well your CV covers them: the spread, not only the middle
    q1, q3 = coverage.quantile([0.25, 0.75])
    spread = alt.Chart(pd.DataFrame({"coverage": coverage}), title=(
        f"CV coverage of DE and AI jobs: median {coverage.median():.0f}%, middle half {q1:.0f}-{q3:.0f}%")).encode(
        x=alt.X("coverage:Q", bin=alt.Bin(step=10, extent=[0, 100]), title="Share of the skills a job asks for that your CV shows",
                axis=alt.Axis(grid=False)),
        y=alt.Y("count():Q", title=None, axis=alt.Axis(gridOpacity=0.15, domain=False, ticks=False)))
    left.altair_chart((spread.mark_bar(color=BLUE, cornerRadiusEnd=3)
                       + alt.Chart(pd.DataFrame({"m": [coverage.median()]})).mark_rule(
                           color=c["ink"], strokeDash=[4, 3], strokeWidth=2).encode(x="m:Q"))
                      .properties(height=220), width="stretch")
# the skills these DE and AI jobs ask for that your CV lacks: what to learn next
read = de_ai[de_ai["described"]]
demand = query("SELECT k.job_id, k.skill, cs.skill IS NOT NULL AS on_cv FROM core.job_skill k"
               " LEFT JOIN (SELECT DISTINCT skill FROM core.cv_skill) cs USING (skill)")
gap = (demand[demand["job_id"].isin(read["job_id"]) & ~demand["on_cv"]].groupby("skill").size()
       .sort_values(ascending=False).head(6).rename("jobs").reset_index())
if len(read) and len(gap):
    gap["share"] = gap["jobs"] / len(read)
    skills = alt.Chart(gap).encode(
        y=alt.Y("skill:N", sort=list(gap["skill"]), title=None, axis=alt.Axis(labelLimit=220, ticks=False, domain=False)),
        x=alt.X("share:Q", title=None, axis=None, scale=alt.Scale(domain=[0, gap["share"].max() * 1.2])),
        tooltip=[alt.Tooltip("skill:N", title="Skill"), alt.Tooltip("share:Q", title="Of the DE and AI jobs read", format=".0%")])
    right.altair_chart((skills.mark_bar(color=ORANGE, cornerRadiusEnd=4, height=18)
                        + skills.mark_text(align="left", dx=4, color=c["ink"]).encode(text=alt.Text("share:Q", format=".0%")))
                       .properties(title=f"Learn next: asked by DE and AI jobs, not on your CV ({len(read)} read)",
                                   height=28 * len(gap)), width="stretch")

# roles by place: a heatmap, the count in each cell, your roles in settings.yaml order
grid = shown.groupby(["role", "place"]).size().reset_index(name="jobs")
heat = alt.Chart(grid, title="Roles by place").encode(
    x=alt.X("place:N", title=None, sort=alt.EncodingSortField("jobs", op="sum", order="descending"),
            axis=alt.Axis(labelAngle=-40, labelOverlap=False, ticks=False, domain=False)),
    y=alt.Y("role:N", title=None, sort=list(shown.drop_duplicates("role").sort_values("role_rank")["role"]),
            axis=alt.Axis(labelLimit=260, labelOverlap=False, ticks=False, domain=False)),
    tooltip=[alt.Tooltip("role:N", title="Role"), alt.Tooltip("place:N", title="Where"), alt.Tooltip("jobs:Q", title="Jobs")])
st.altair_chart((heat.mark_rect(cornerRadius=3, stroke=c["surface"], strokeWidth=2).encode(
    color=alt.Color("jobs:Q", title="Jobs", scale=alt.Scale(range=c["heat"]), legend=None))
    + heat.mark_text(fontSize=12).encode(
        text="jobs:Q", color=alt.condition(alt.datum.jobs > grid["jobs"].max() / 2, alt.value(c["heat_ink"]), alt.value(c["ink"]))))
    .properties(height=36 * grid["role"].nunique() + 70), width="stretch")
