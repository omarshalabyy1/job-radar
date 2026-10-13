"""Skills: which of your skills the jobs your filters keep ask for, and which they ask for that your CV lacks."""

import altair as alt
import streamlit as st

from tracker.common import BLUE, ORANGE, colors, filtered, load_jobs, query

st.title("Skills", icon=":material/school:")
pool = filtered(load_jobs())
pool = pool[pool["described"]]
demand = query("SELECT k.job_id, k.skill, s.track, cs.skill IS NOT NULL AS on_cv FROM core.job_skill k"
               " JOIN core.skill s USING (skill) LEFT JOIN (SELECT DISTINCT skill FROM core.cv_skill) cs USING (skill)")
track = st.segmented_control("Track", ["All", "Data Engineering", "BI & Analytics", "Generative AI"], default="All",
                             key="track", label_visibility="collapsed")
asked = demand[demand["job_id"].isin(pool["job_id"]) & ((demand["track"] == track) if track and track != "All" else True)]
if asked.empty:
    st.info("No job with a description matches these filters yet: the skills come from the descriptions the describe "
            "step reads.", icon=":material/info:")
    st.stop()

top = asked.groupby(["skill", "on_cv"]).size().reset_index(name="jobs").sort_values("jobs", ascending=False).head(15)
top["share"] = top["jobs"] / len(pool)
top["Your CV"] = top["on_cv"].map({True: "On your CV", False: "Not on your CV yet"})
gaps = top[~top["on_cv"]]
with st.container(horizontal=True):
    st.metric("Jobs read", len(pool), border=True, width=170, help="Jobs with a description that your filters keep")
    st.metric("Top skills on your CV", f"{int(top['on_cv'].sum())} of {len(top)}", border=True, width=200)
    st.metric("Most asked skill you lack", gaps.iloc[0]["skill"] if len(gaps) else "None", border=True, width=260,
              delta=f"{gaps.iloc[0]['share']:.0%} of jobs ask for it" if len(gaps) else None, delta_color="off")
base = alt.Chart(top).encode(
    y=alt.Y("skill:N", sort=list(top["skill"]), title=None, axis=alt.Axis(labelLimit=240, ticks=False, domain=False)),
    x=alt.X("share:Q", title="Share of the jobs read that ask for it", axis=alt.Axis(format="%", grid=False),
            scale=alt.Scale(domain=[0, top["share"].max() * 1.15])),
    tooltip=[alt.Tooltip("skill:N", title="Skill"), alt.Tooltip("jobs:Q", title="Jobs asking"),
             alt.Tooltip("share:Q", title="Share", format=".0%"), alt.Tooltip("Your CV:N")])
st.altair_chart(
    (base.mark_bar(cornerRadiusEnd=4, height=18).encode(color=alt.Color(
        "Your CV:N", title=None, legend=alt.Legend(orient="top"),
        scale=alt.Scale(domain=["On your CV", "Not on your CV yet"], range=[BLUE, ORANGE])))
     + base.mark_text(align="left", dx=4, color=colors()["ink"]).encode(text=alt.Text("share:Q", format=".0%")))
    .properties(title="The skills these jobs ask for most", height=28 * len(top)),
    width="stretch", height=28 * len(top) + 110)  # Streamlit fits a chart to its element: give it the room
st.caption("Each bar: of the jobs read, the share whose title or description names the skill. Blue skills are on your CV, "
           "orange ones are not yet.")
