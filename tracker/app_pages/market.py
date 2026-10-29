"""Market: what the jobs your filters keep say about the market, data engineering and AI first. Blue is DE and AI,
gray is everything else; orange only marks a skill your CV lacks."""

import re
from datetime import timedelta
from itertools import combinations

import altair as alt
import pandas as pd
import streamlit as st

from job_radar.config import KEEP_DAYS, ROLE_LABEL
from tracker.common import BLUE, GRAY, ORANGE, PRIORITY_ROLES, colors, filtered, load_jobs, nothing_matches, query

st.title("Market", icon=":material/monitoring:")
jobs = load_jobs()
shown = filtered(jobs)
if shown.empty:
    nothing_matches()
    st.stop()
c = colors()
INK = c["ink"]
GROUPS = ["DE and AI", "Other roles"]
emphasis = alt.Scale(domain=GROUPS, range=[BLUE, GRAY])
GRID = alt.Axis(gridOpacity=0.15, domain=False, ticks=False)


def seniority(title: str) -> str:
    t = title.lower()
    if re.search(r"\b(intern|trainee|junior|jr|graduate|entry)\b", t):
        return "Junior"
    if re.search(r"\b(senior|sr|lead|staff|principal|head|manager|architect)\b", t):
        return "Senior"
    return "Mid or not stated"


def bars(frame: pd.DataFrame, label: str, value: str, title: str, color: str = BLUE, fmt: str = "d") -> alt.LayerChart:
    base = alt.Chart(frame).encode(
        y=alt.Y(f"{label}:N", sort=list(frame[label]), title=None, axis=alt.Axis(labelLimit=240, ticks=False, domain=False)),
        x=alt.X(f"{value}:Q", title=None, axis=None, scale=alt.Scale(domain=[0, max(frame[value].max(), 1e-9) * 1.18])),
        tooltip=[alt.Tooltip(f"{label}:N"), alt.Tooltip(f"{value}:Q", format=fmt)])
    return (base.mark_bar(color=color, cornerRadiusEnd=4, height=18)
            + base.mark_text(align="left", dx=4, color=INK).encode(text=alt.Text(f"{value}:Q", format=fmt))).properties(
        title=title, height=28 * len(frame))


prio = shown["role_rank"] <= PRIORITY_ROLES
shown = shown.assign(group=prio.map({True: GROUPS[0], False: GROUPS[1]}), level=shown["title"].map(seniority))
de_ai = shown[prio]
coverage = de_ai["cv_coverage"].dropna()
today = pd.Timestamp.now(tz="Africa/Cairo").date()
per_day = de_ai.groupby("first_seen").size()
# the last KEEP_DAYS days, all still in core.job (an older day keeps only the jobs with a status), 0 for a day with none
earlier = per_day.reindex([today - timedelta(days=n) for n in range(KEEP_DAYS, 0, -1)], fill_value=0)
new_today = int(per_day.get(today, 0))

# what the boards offered that you could not take: the extract steps' own counts
steps = query("SELECT DISTINCT ON (step) step, summary FROM core.step_run WHERE step LIKE 'extract_%' ORDER BY step, started_at DESC")
offered = []
for text in steps["summary"].fillna(""):
    for source, found, dropped in re.findall(r"(\w+): (\d+) postings, \d+ new(?:, (\d+) not for Egypt dropped)?", text):
        offered.append({"Board": source, "Postings": int(found), "Not for Egypt": int(dropped or 0)})
offered = pd.DataFrame(offered)
closed_share = offered["Not for Egypt"].sum() / max(offered["Postings"].sum(), 1) if len(offered) else None

with st.container(horizontal=True):
    st.metric("DE and AI jobs", len(de_ai), border=True, width=170, delta=f"{len(de_ai) / len(shown):.0%} of {len(shown)}", delta_color="off",
              help="Jobs in your first three roles in settings.yaml, of the jobs your filters keep")
    st.metric("New today", new_today, border=True, width=170,
              delta=f"{new_today - earlier.mean():+.0f} vs a usual day" if (per_day.index < today).any() else None,
              help=f"DE and AI jobs first found today, against the average of the last {KEEP_DAYS} days")
    st.metric("Remote", f"{(de_ai['place'] == 'Remote').mean():.0%}" if len(de_ai) else "n/a", border=True, width=170,
              help="Share of the DE and AI jobs open to anywhere, or to Egypt")
    st.metric("Median CV coverage", f"{coverage.median():.0f}%" if len(coverage) else "n/a", border=True, width=200,
              help="Of the skills a DE or AI job asks for, how many your best CV shows; the middle job (jobs with a description)")
    st.metric("Postings you cannot take", f"{closed_share:.0%}" if closed_share is not None else "n/a", border=True, width=230,
              help="Of what the boards returned in the latest run: jobs tied to the USA, Canada or Europe, or limited to other countries")

# ---------------------------------------------------------------- 1. demand
st.subheader("Demand")
left, right = st.columns(2, gap="large")
days = shown.groupby(["first_seen", "group"]).size().reset_index(name="jobs")
days["day"] = pd.to_datetime(days["first_seen"])
left.altair_chart(alt.Chart(days, title="New jobs a day").mark_bar(cornerRadiusEnd=3, size=26).encode(
    x=alt.X("day:T", title=None, axis=alt.Axis(format="%a %d", tickCount="day", grid=False)),
    y=alt.Y("jobs:Q", title=None, axis=GRID),
    color=alt.Color("group:N", title=None, sort=GROUPS, legend=alt.Legend(orient="top"), scale=emphasis),
    order=alt.Order("group:N", sort="ascending"),
    tooltip=[alt.Tooltip("day:T", format="%A %d %B"), alt.Tooltip("group:N", title="Roles"), alt.Tooltip("jobs:Q")]).properties(height=240),
    width="stretch")
where = shown.groupby(["place", "group"]).size().reset_index(name="jobs")
order = list(where[where["group"] == GROUPS[0]].sort_values("jobs", ascending=False)["place"])
order += [p for p in where["place"].unique() if p not in order]
right.altair_chart(alt.Chart(where, title="Where the jobs are").mark_bar(cornerRadiusEnd=3, height=18).encode(
    y=alt.Y("place:N", sort=order, title=None, axis=alt.Axis(labelLimit=200, ticks=False, domain=False)),
    x=alt.X("jobs:Q", title=None, axis=GRID),
    color=alt.Color("group:N", title=None, sort=GROUPS, legend=alt.Legend(orient="top"), scale=emphasis),
    order=alt.Order("group:N", sort="ascending"),
    tooltip=[alt.Tooltip("place:N", title="Where"), alt.Tooltip("group:N", title="Roles"), alt.Tooltip("jobs:Q")]
).properties(height=alt.Step(28)), width="stretch")

# ------------------------------------------------------- 2. the roles, in depth
st.subheader("The roles, in depth")
card = (shown.groupby(["role_rank", "role"]).agg(
    Jobs=("job_id", "size"), Remote=("place", lambda s: 100 * (s == "Remote").mean()),
    Senior=("level", lambda s: 100 * (s == "Senior").mean()), Junior=("level", lambda s: 100 * (s == "Junior").mean()),
    Skills=("skill_matches", "median"), Coverage=("cv_coverage", "median"), Score=("score", "median"),
    Fresh=("fresh_level", lambda s: 100 * (s <= 3).mean())).reset_index().sort_values("role_rank").drop(columns="role_rank"))
st.dataframe(card, hide_index=True, width="stretch", column_config={
    "role": "Role", "Jobs": st.column_config.NumberColumn("Jobs", format="%d"),
    "Remote": st.column_config.ProgressColumn("Remote", min_value=0, max_value=100, format="%d%%"),
    "Senior": st.column_config.ProgressColumn("Senior titles", min_value=0, max_value=100, format="%d%%",
                                              help="Senior, lead, staff, principal, head, manager or architect in the title"),
    "Junior": st.column_config.ProgressColumn("Junior titles", min_value=0, max_value=100, format="%d%%",
                                              help="Intern, trainee, junior or graduate in the title"),
    "Skills": st.column_config.NumberColumn("Your skills asked (median)", format="%.0f"),
    "Coverage": st.column_config.ProgressColumn("CV coverage (median)", min_value=0, max_value=100, format="%d%%"),
    "Score": st.column_config.NumberColumn("Claude score (median)", format="%.0f", help="Empty when no job of the role is graded"),
    "Fresh": st.column_config.ProgressColumn("Posted within 48h", min_value=0, max_value=100, format="%d%%")})
left, right = st.columns(2, gap="large")
mix = shown.groupby(["role", "level"]).size().reset_index(name="jobs")
mix["share"] = mix["jobs"] / mix.groupby("role")["jobs"].transform("sum")
role_order = list(shown.drop_duplicates("role").sort_values("role_rank")["role"])
left.altair_chart(alt.Chart(mix, title="Seniority by role, read from the titles").mark_bar(height=22).encode(
    y=alt.Y("role:N", sort=role_order, title=None, axis=alt.Axis(labelLimit=220, ticks=False, domain=False)),
    x=alt.X("share:Q", title=None, axis=alt.Axis(format="%", gridOpacity=0.15, domain=False, ticks=False), stack="normalize"),
    color=alt.Color("level:N", title=None, sort=["Junior", "Mid or not stated", "Senior"], legend=alt.Legend(orient="top"),
                    scale=alt.Scale(domain=["Junior", "Mid or not stated", "Senior"], range=["#86b6ef", GRAY, BLUE])),
    tooltip=["role:N", "level:N", alt.Tooltip("jobs:Q"), alt.Tooltip("share:Q", format=".0%")]).properties(height=alt.Step(34)),
    width="stretch")
if len(coverage):
    graded = shown.dropna(subset=["cv_coverage"])
    right.altair_chart(alt.Chart(graded, title="CV coverage by role: the middle half and the extremes").mark_boxplot(
        extent="min-max", size=18, color=BLUE).encode(
        y=alt.Y("role:N", sort=role_order, title=None, axis=alt.Axis(labelLimit=220, ticks=False, domain=False)),
        x=alt.X("cv_coverage:Q", title="Share of the skills a job asks for that your CV shows (%)", scale=alt.Scale(domain=[0, 100]),
                axis=alt.Axis(gridOpacity=0.15)),
        tooltip=["role:N"]).properties(height=alt.Step(34)), width="stretch")

# ------------------------------------------------------------ 3. employers
st.subheader("Employers")
left, right = st.columns(2, gap="large")
named = de_ai[de_ai["company"] != ""]
hiring = (named.groupby("company").agg(jobs=("job_id", "size"), target=("target_company", "max"))
          .sort_values("jobs", ascending=False).head(10).reset_index())
if len(hiring):
    hiring["label"] = hiring["company"] + hiring["target"].map({True: " ★", False: ""})
    left.altair_chart(bars(hiring, "label", "jobs", "Who is hiring for DE and AI (★ your target companies)"), width="stretch")
    top5 = named["company"].value_counts().head(5).sum() / max(len(named), 1)
    left.caption(f"{named['company'].nunique()} companies share the {len(named)} named DE and AI jobs; the top 5 hold {top5:.0%} of them.")
boards = (shown[shown["source"] != ""].groupby("source").agg(jobs=("job_id", "size"), de_ai=("group", lambda s: (s == GROUPS[0]).sum()),
                                                            score=("score", "median"), described=("described", "mean")).reset_index())
boards["share"] = 100 * boards["de_ai"] / boards["jobs"]
boards["described"] = 100 * boards["described"]
right.markdown("**Which boards deliver DE and AI jobs**")
right.dataframe(boards.sort_values(["de_ai", "jobs"], ascending=False).head(10), hide_index=True, width="stretch", column_config={
    "source": "Board", "jobs": st.column_config.NumberColumn("Jobs", format="%d"),
    "de_ai": st.column_config.NumberColumn("DE and AI", format="%d"),
    "share": st.column_config.ProgressColumn("DE and AI share", min_value=0, max_value=100, format="%d%%"),
    "score": st.column_config.NumberColumn("Median score", format="%.0f", help="Claude's middle score; empty when none is graded"),
    "described": st.column_config.ProgressColumn("With a description", min_value=0, max_value=100, format="%d%%",
                                                 help="Only a described job can be graded or have its skills read")})
if len(offered):
    long = offered.assign(Open=offered["Postings"] - offered["Not for Egypt"]).melt(
        id_vars="Board", value_vars=["Open", "Not for Egypt"], var_name="Kind", value_name="Count")
    long = long[long["Count"] > 0]
    totals = offered.set_index("Board")["Postings"].sort_values(ascending=False)
    st.altair_chart(alt.Chart(long, title="What each board returned in the latest run, and how much of it you cannot take").mark_bar(
        cornerRadiusEnd=3, height=20).encode(
        y=alt.Y("Board:N", sort=list(totals.index), title=None, axis=alt.Axis(ticks=False, domain=False)),
        x=alt.X("Count:Q", title=None, axis=GRID),
        color=alt.Color("Kind:N", title=None, sort=["Open", "Not for Egypt"], legend=alt.Legend(orient="top"),
                        scale=alt.Scale(domain=["Open", "Not for Egypt"], range=[BLUE, GRAY])),
        order=alt.Order("Kind:N", sort="descending"),
        tooltip=["Board:N", "Kind:N", "Count:Q"]).properties(height=alt.Step(26)), width="stretch")

# ------------------------------------------------------------------ 4. skills
st.subheader("Skills")
read = de_ai[de_ai["described"]]
demand = query("SELECT k.job_id, k.skill, s.track, cs.skill IS NOT NULL AS on_cv FROM core.job_skill k JOIN core.skill s USING (skill)"
               " LEFT JOIN (SELECT DISTINCT skill FROM core.cv_skill) cs USING (skill)")
asked = demand[demand["job_id"].isin(read["job_id"])]
if len(read) and len(asked):
    left, right = st.columns(2, gap="large")
    top = asked.groupby(["skill", "on_cv"]).size().reset_index(name="jobs").sort_values("jobs", ascending=False).head(14)
    top["share"] = top["jobs"] / len(read)
    top["Your CV"] = top["on_cv"].map({True: "On your CV", False: "Not on your CV yet"})
    base = alt.Chart(top).encode(
        y=alt.Y("skill:N", sort=list(top["skill"]), title=None, axis=alt.Axis(labelLimit=220, ticks=False, domain=False)),
        x=alt.X("share:Q", title=None, axis=None, scale=alt.Scale(domain=[0, top["share"].max() * 1.2])),
        tooltip=[alt.Tooltip("skill:N"), alt.Tooltip("jobs:Q", title="Jobs asking"), alt.Tooltip("share:Q", format=".0%"), "Your CV:N"])
    left.altair_chart((base.mark_bar(cornerRadiusEnd=4, height=18).encode(color=alt.Color(
        "Your CV:N", title=None, legend=alt.Legend(orient="top"),
        scale=alt.Scale(domain=["On your CV", "Not on your CV yet"], range=[BLUE, ORANGE])))
        + base.mark_text(align="left", dx=4, color=INK).encode(text=alt.Text("share:Q", format=".0%")))
        .properties(title=f"What DE and AI jobs ask for most ({len(read)} read)", height=26 * len(top)), width="stretch")
    # the skill by role: which roles ask for which skill
    names = list(top["skill"].head(10))
    cross = asked[asked["skill"].isin(names)].merge(read[["job_id", "role"]], on="job_id").groupby(["skill", "role"]).size().reset_index(name="jobs")
    cross["share"] = cross["jobs"] / cross["role"].map(read.groupby("role").size())
    heat = alt.Chart(cross, title="Share of each role's jobs that ask for the skill").encode(
        x=alt.X("role:N", title=None, axis=alt.Axis(labelAngle=-30, labelLimit=160, ticks=False, domain=False)),
        y=alt.Y("skill:N", sort=names, title=None, axis=alt.Axis(ticks=False, domain=False)),
        tooltip=["skill:N", "role:N", alt.Tooltip("share:Q", format=".0%"), alt.Tooltip("jobs:Q")])
    right.altair_chart((heat.mark_rect(cornerRadius=3, stroke=c["surface"], strokeWidth=2).encode(
        color=alt.Color("share:Q", scale=alt.Scale(range=c["heat"]), legend=None))
        + heat.mark_text(fontSize=11).encode(text=alt.Text("share:Q", format=".0%"), color=alt.condition(
            alt.datum.share > 0.5, alt.value(c["heat_ink"]), alt.value(INK)))).properties(height=alt.Step(26)), width="stretch")
    # the skills asked together: what a job that asks for one also asks for
    pairs = {}
    for _, skills in asked.groupby("job_id")["skill"]:
        for a, b in combinations(sorted(set(skills)), 2):
            pairs[(a, b)] = pairs.get((a, b), 0) + 1
    left, right = st.columns(2, gap="large")
    if pairs:
        pair = pd.DataFrame([{"Pair": f"{a} + {b}", "jobs": n} for (a, b), n in pairs.items()]).sort_values("jobs", ascending=False).head(10)
        left.altair_chart(bars(pair, "Pair", "jobs", "Skills asked together"), width="stretch")
    gap = asked[~asked["on_cv"]].groupby("skill").size().sort_values(ascending=False).head(8).rename("jobs").reset_index()
    if len(gap):
        gap["share"] = gap["jobs"] / len(read)
        right.altair_chart(bars(gap, "skill", "share", "Learn next: asked by DE and AI jobs, not on your CV", color=ORANGE, fmt=".0%"),
                           width="stretch")
else:
    st.info("No DE or AI job with a description matches these filters yet: the skills come from the descriptions the describe step reads.",
            icon=":material/info:")

# ------------------------------------------------------------------- 5. fit
st.subheader("Fit")
left, right = st.columns(2, gap="large")
fit = shown.dropna(subset=["cv_coverage"]).assign(label=lambda d: d["title"].str.slice(0, 48) + " @ " + d["company"].replace("", "company hidden"))
if len(fit):
    left.altair_chart(alt.Chart(fit, title="Where the best fits are: skills asked against CV coverage").mark_circle(opacity=0.75).encode(
        x=alt.X("skill_matches:Q", title="Your skills the job asks for", axis=alt.Axis(gridOpacity=0.15, tickMinStep=1)),
        y=alt.Y("cv_coverage:Q", title="CV coverage (%)", scale=alt.Scale(domain=[0, 105]), axis=alt.Axis(gridOpacity=0.15)),
        size=alt.value(70), color=alt.Color("group:N", title=None, sort=GROUPS, legend=alt.Legend(orient="top"), scale=emphasis),
        tooltip=["label:N", "role:N", alt.Tooltip("skill_matches:Q", title="Skills asked"),
                 alt.Tooltip("cv_coverage:Q", format=".0f", title="CV coverage"), alt.Tooltip("score:Q", title="Score")]).properties(height=300),
        width="stretch")
scored = de_ai.dropna(subset=["score"])
if len(scored):
    dist = scored["score"].astype(int).value_counts().reindex(range(0, 11), fill_value=0).rename_axis("Score").reset_index(name="jobs")
    right.altair_chart(alt.Chart(dist, title=f"Claude's scores for the {len(scored)} graded DE and AI jobs").mark_bar(
        color=BLUE, cornerRadiusEnd=3).encode(
        x=alt.X("Score:O", title=None, axis=alt.Axis(labelAngle=0)), y=alt.Y("jobs:Q", title=None, axis=GRID),
        tooltip=["Score:O", "jobs:Q"]).properties(height=300), width="stretch")
    right.caption(f"Median {scored['score'].median():.0f}; {int((scored['score'] >= 8).sum())} job(s) scored 8 or more, the ones tailor drafts for.")

# ---------------------------------------------------------------- 6. map
st.subheader("Roles by place")
grid = shown.groupby(["role", "place"]).size().reset_index(name="jobs")
heat = alt.Chart(grid).encode(
    x=alt.X("place:N", title=None, sort=alt.EncodingSortField("jobs", op="sum", order="descending"),
            axis=alt.Axis(labelAngle=-40, labelOverlap=False, ticks=False, domain=False)),
    y=alt.Y("role:N", title=None, sort=role_order, axis=alt.Axis(labelLimit=260, labelOverlap=False, ticks=False, domain=False)),
    tooltip=["role:N", "place:N", alt.Tooltip("jobs:Q")])
st.altair_chart((heat.mark_rect(cornerRadius=3, stroke=c["surface"], strokeWidth=2).encode(
    color=alt.Color("jobs:Q", scale=alt.Scale(range=c["heat"]), legend=None))
    + heat.mark_text(fontSize=12).encode(text="jobs:Q", color=alt.condition(
        alt.datum.jobs > grid["jobs"].max() / 2, alt.value(c["heat_ink"]), alt.value(INK)))).properties(height=36 * grid["role"].nunique() + 70),
    width="stretch")

# ------------------------------------------------------------ 7. over time
st.subheader("Over time")
history = query("SELECT date_trunc('week', first_seen)::date AS week, role, count(*) AS jobs,"
                " avg(closed_on - first_seen) FILTER (WHERE closed_on IS NOT NULL) AS days_open,"
                " count(closed_on) AS closed FROM core.job_history GROUP BY 1, 2 ORDER BY 1")
if history.empty:
    st.caption("No history yet: every collect adds the jobs it keeps (core.job_history), kept after the jobs are deleted.")
else:
    history = history.assign(group=history["role"].isin([ROLE_LABEL[r] for r in range(1, PRIORITY_ROLES + 1)]).map({True: GROUPS[0], False: GROUPS[1]}))
    weekly = history.groupby(["week", "group"], as_index=False)["jobs"].sum()
    st.altair_chart(alt.Chart(weekly).mark_line(point=True).encode(
        x=alt.X("week:T", title=None, axis=GRID), y=alt.Y("jobs:Q", title="Jobs first seen", axis=GRID),
        color=alt.Color("group:N", scale=emphasis, legend=alt.Legend(title=None, orient="top")),
        tooltip=[alt.Tooltip("week:T", title="Week of"), "group:N", "jobs:Q"]).properties(
        title="Jobs a week", height=220), width="stretch")
    closed = history[history["closed"] > 0]
    if closed.empty:
        st.caption("Time to close: no job page has been found closed yet.")
    else:
        by_role = (closed.assign(total=closed["days_open"].astype(float) * closed["closed"])
                   .groupby("role", as_index=False)[["total", "closed"]].sum())
        by_role = by_role.assign(days=by_role["total"] / by_role["closed"]).sort_values("days")
        st.altair_chart(bars(by_role, "role", "days", "Days from first seen to closed, by role (an estimate: "
                             "a page is checked once, when describe reads it)", fmt=".1f"), width="stretch")
