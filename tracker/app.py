"""Job tracker: every job the radar found, your status for each, which of your skills are in demand,
and a dashboard of the jobs your filters keep.

Run by docker-compose.yml on http://127.0.0.1:8501, or from the repo root with .env set:
    streamlit run tracker/app.py
"""

import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")
from job_radar.db import connect  # noqa: E402

STATUSES = ["new", "saved", "applied", "interview", "offer", "rejected", "ignored"]
OPEN = ["new", "saved", "applied", "interview", "offer"]
# Apply within 48 hours of the posting: mart.job_status.fresh_level, from when the job was posted
FRESH = {1: "Within 12 hours", 2: "Within 24 hours", 3: "Within 48 hours", 4: "Over 48 hours"}
# Sort by any of these, in the order picked: ↓ is highest, newest or Z first; ↑ is lowest, oldest or A first
SORTS = {"Role": "role_rank", "Target": "target_company", "Skills": "skill_matches", "CV covers": "cv_coverage",
         "Posted": "posted_at", "First seen": "first_seen", "Title": "title", "Company": "company", "Where": "place",
         "Source": "source", "Status": "status"}
SORT_OPTIONS = [f"{name} {arrow}" for name in SORTS for arrow in ("↓", "↑")]
DEFAULT_SORT = ["Role ↑", "Target ↓", "Skills ↓", "Posted ↓"]
# the sidebar's list filters: widget key -> column
FILTERS = {"role": "role", "where": "place", "posted": "posted", "status": "status"}
# chart colors (the dataviz skill's palette, dark-surface steps): one blue for a single series; blue and
# orange for "on your CV" and "not on your CV yet"
BLUE, ORANGE = "#3987e5", "#d95926"


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def query(sql: str) -> pd.DataFrame:
    with connect() as conn:
        cur = conn.execute(sql)
        return pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])


st.set_page_config(page_title="Job tracker", page_icon=":material/work:", layout="wide")
st.title("Job tracker")
LIGHT = st.context.theme.type == "light"
INK = "#31333f" if LIGHT else "#e6e6e6"  # chart text: never a series color
SURFACE = "#ffffff" if LIGHT else "#0e1117"  # the page behind the charts, for the gaps between cells
# one blue ramp: the freshest jobs brightest on a dark page (darkest on a light one); the heatmap from faint to strong
FRESH_COLORS = ["#184f95", "#256abf", "#3987e5", "#86b6ef"][::-1] if not LIGHT else ["#0d366b", "#1c5cab", "#3987e5", "#86b6ef"]
HEAT = ["#eef4fc", "#1c5cab"] if LIGHT else ["#1a2738", "#86b6ef"]
HEAT_INK = "#ffffff" if LIGHT else "#0d1b2a"  # the count on the strongest cells

jobs = query("SELECT job_id, role_rank, fresh_level, posted_at, skill_matches, cv_coverage, target_company, title, company,"
             " role, place, source, status, note, first_seen, job_url, skills_matched, described FROM mart.job_status")
jobs["posted"] = jobs["fresh_level"].map(FRESH)
jobs["cv_coverage"] = pd.to_numeric(jobs["cv_coverage"])

OPTIONS = {"role": sorted(jobs["role"].unique()), "where": sorted(jobs["place"].unique()),
           "posted": list(FRESH.values()), "status": STATUSES}
DEFAULTS = {"role": [], "where": [], "posted": [], "status": OPEN, "skills": 0, "q": "", "sort": DEFAULT_SORT}
# The filters live in the address, so a reload or a bookmark keeps them.
if "role" not in st.session_state:
    url = st.query_params
    skills_in_url = url.get("skills", "0")
    st.session_state.update(
        {name: [o for o in url.get_all(name) if o in OPTIONS[name]] or list(DEFAULTS[name]) for name in FILTERS},
        skills=min(int(skills_in_url), 10) if skills_in_url.isdigit() else 0,
        q=url.get("q", ""),
        sort=[o for o in url.get_all("sort") if o in SORT_OPTIONS] or list(DEFAULT_SORT))


def reset() -> None:
    st.session_state.update({name: list(v) if isinstance(v, list) else v for name, v in DEFAULTS.items()})


def passing(skip: str = "") -> pd.Series:
    """The jobs every filter keeps, leaving out the filter named skip (for that filter's counts)."""
    f = st.session_state
    keep = jobs["skill_matches"] >= f.skills
    for name, column in FILTERS.items():
        if f[name] and name != skip:
            keep &= jobs[column].isin(f[name])
    if f.q:
        keep &= (jobs["title"] + " " + jobs["company"]).str.contains(f.q, case=False, regex=False)
    return keep


def option_counts(name: str) -> pd.Series:
    """How many jobs each option of a filter gives with the other filters as they are, in option order."""
    return jobs.loc[passing(skip=name), FILTERS[name]].value_counts().reindex(OPTIONS[name], fill_value=0)


def counts_line(name: str) -> None:
    # Counts sit under a filter, not in its option labels: a label that changes makes Streamlit treat the
    # filter as a new widget and clear what you picked.
    n = option_counts(name)
    st.caption(" · ".join(f"{option} **{count}**" for option, count in n[n > 0].items()) or "no jobs")


with st.sidebar:
    st.button("Reset filters", icon=":material/restart_alt:", on_click=reset, width="stretch")
    roles = st.multiselect("Role", OPTIONS["role"], key="role")
    counts_line("role")
    places = st.multiselect("Where", OPTIONS["where"], key="where")
    counts_line("where")
    posted = st.multiselect("Posted", OPTIONS["posted"], key="posted",
                            help="Apply within 48 hours of the posting. When a site gives no time, the time the "
                                 "radar first found the job counts (or the posting date, when that is older).")
    counts_line("posted")
    statuses = st.multiselect("Status", STATUSES, key="status")
    counts_line("status")
    min_skills = st.slider("At least this many of your skills", 0, 10, key="skills")
    text = st.text_input("Search title or company", key="q")
    sort = st.multiselect("Sort by", SORT_OPTIONS, key="sort",
                          help="First pick first: ↓ is highest, newest or Z first, ↑ lowest, oldest or A first "
                               "(Role ↑ is your order in settings.yaml). A column picked twice counts once.")
st.query_params.from_dict({k: v for k, v in {"role": roles, "where": places, "posted": posted, "status": statuses,
                                             "skills": min_skills, "q": text, "sort": sort}.items() if v})

shown = jobs[passing()]
by, ascending = [], []
for choice in sort:
    name, arrow = choice.rsplit(" ", 1)
    if SORTS[name] not in by:
        by.append(SORTS[name])
        ascending.append(arrow == "↑")
if by:
    shown = shown.sort_values(by, ascending=ascending, na_position="last", kind="stable",
                              key=lambda c: c.str.lower() if pd.api.types.is_string_dtype(c) else c)

counts = jobs["status"].value_counts()
for column, status in zip(st.columns(5), ["new", "saved", "applied", "interview", "offer"]):
    column.metric(status.title(), int(counts.get(status, 0)))


def bars(data: pd.DataFrame, column: str, title: str, order: list | None = None, top: int = 10) -> alt.LayerChart:
    """Jobs per value of a column, as labeled horizontal bars (most first, or in the given order)."""
    n = data[column].value_counts()
    n = (n.reindex(order, fill_value=0) if order else n.head(top)).rename_axis("value").reset_index(name="jobs")
    base = alt.Chart(n).encode(
        y=alt.Y("value:N", sort=order or "-x", title=None, axis=alt.Axis(labelLimit=220, ticks=False, domain=False)),
        x=alt.X("jobs:Q", title=None, axis=None, scale=alt.Scale(domain=[0, max(n["jobs"].max(), 1) * 1.15])),
        tooltip=[alt.Tooltip("value:N", title=title), alt.Tooltip("jobs:Q", title="Jobs")])
    return ((base.mark_bar(color=BLUE, cornerRadiusEnd=4, height=18)
             + base.mark_text(align="left", dx=4, color=INK).encode(text="jobs:Q"))
            .properties(title=title, height=28 * len(n)))


jobs_tab, dashboard_tab, skills_tab, companies_tab, cv_tab = st.tabs(
    ["Jobs", "Dashboard", "Skills in demand", "Companies", "CV"])

with jobs_tab:
    st.caption(f"{plural(len(shown), 'job')} of {len(jobs)} match these filters")
    if shown.empty:
        empty = [o for name in FILTERS for o in st.session_state[name] if option_counts(name)[o] == 0]
        st.info("No jobs match these filters"
                + (f": {', '.join(empty)} {'has' if len(empty) == 1 else 'have'} no jobs with the other filters"
                   if empty else "")
                + ". Remove one, or press Reset filters in the sidebar.")
    else:
        edited = st.data_editor(
            shown, key="jobs", hide_index=True,
            column_order=["posted", "posted_at", "skill_matches", "cv_coverage", "target_company", "title", "company",
                          "role", "place", "source", "status", "note", "first_seen", "job_url", "skills_matched"],
            disabled=[c for c in shown.columns if c not in ("status", "note")],
            column_config={
                "posted": st.column_config.TextColumn("Posted", help="Apply within 48 hours of the posting"),
                "posted_at": st.column_config.DatetimeColumn("Posted at", format="D MMM, h:mma"),
                "skill_matches": st.column_config.NumberColumn("Skills", help="How many of your skills the job asks for"),
                "cv_coverage": st.column_config.ProgressColumn("CV covers", min_value=0, max_value=100, format="%d%%",
                                                               help="How much of what the job asks for your best CV shows"),
                "target_company": st.column_config.CheckboxColumn("Target"),
                "status": st.column_config.SelectboxColumn("Status", options=STATUSES, required=True),
                "job_url": st.column_config.LinkColumn("Link", display_text="View job"),
                "first_seen": "First seen",
                "skills_matched": "Your skills it asks for",
            })
        changed = edited[(edited["status"] != shown["status"]) | (edited["note"].fillna("") != shown["note"].fillna(""))]
        if st.button(f"Save {plural(len(changed), 'change')}", type="primary", disabled=changed.empty):
            with connect() as conn:
                for job in changed.itertuples():
                    note = job.note.strip() if isinstance(job.note, str) and job.note.strip() else None
                    # A job back at "new" with no note needs no row; one with a note keeps it under "new".
                    if job.status == "new" and note is None:
                        conn.execute("DELETE FROM core.application WHERE job_id = %s", (job.job_id,))
                    else:
                        conn.execute(
                            "INSERT INTO core.application (job_id, status, note) VALUES (%s, %s, %s)"
                            " ON CONFLICT (job_id) DO UPDATE SET status = EXCLUDED.status, note = EXCLUDED.note,"
                            " updated_at = now()", (job.job_id, job.status, note))
            st.rerun()

with dashboard_tab:
    st.caption(f"The {plural(len(shown), 'job')} your sidebar filters keep.")
    if shown.empty:
        st.info("No jobs match these filters. Remove one, or press Reset filters in the sidebar.")
    else:
        fresh = int((shown["fresh_level"] <= 3).sum())
        k1, k2, k3, k4, k5 = st.columns(5, border=True)
        k1.metric("Jobs", len(shown))
        k2.metric("Within 48 hours", fresh, f"{fresh / len(shown):.0%} of them", delta_color="off",
                  help="The ones to apply to first")
        k3.metric("At your companies", int(shown["target_company"].sum()))
        k4.metric("Remote", int((shown["place"] == "Remote").sum()), help="Open to anywhere, or to Egypt")
        coverage = shown["cv_coverage"].median()
        k5.metric("Median CV coverage", "n/a" if pd.isna(coverage) else f"{coverage:.0f}%",
                  help="Of the skills a job asks for, how many your best CV shows (jobs with a description)")
        left, right = st.columns(2, gap="large")
        # how fresh: a donut, freshest brightest (one blue, in order)
        n = shown["posted"].value_counts().reindex(FRESH.values(), fill_value=0).rename_axis("posted").reset_index(name="jobs")
        n["label"] = n["posted"] + " · " + n["jobs"].astype(str)
        left.altair_chart(alt.Chart(n, title="How fresh: apply within 48 hours").mark_arc(
            innerRadius=70, padAngle=0.02, cornerRadius=3).encode(
            theta=alt.Theta("jobs:Q", stack=True),
            color=alt.Color("label:N", title=None, sort=list(n["label"]), legend=alt.Legend(orient="right"),
                            scale=alt.Scale(domain=list(n["label"]), range=FRESH_COLORS)),
            order=alt.Order("order:Q"),
            tooltip=[alt.Tooltip("posted:N", title="Posted"), alt.Tooltip("jobs:Q", title="Jobs")])
            .transform_calculate(order="indexof(" + str(list(n["label"])) + ", datum.label)")
            .properties(height=260), width="stretch")
        # new jobs a day: a line over the days the radar ran
        daily = shown.groupby("first_seen").size().reset_index(name="jobs")
        daily["day"] = pd.to_datetime(daily["first_seen"])
        line = alt.Chart(daily, title="New jobs a day").encode(
            x=alt.X("day:T", title=None, axis=alt.Axis(format="%a %d %b", tickCount="day", grid=False)),
            y=alt.Y("jobs:Q", title=None, axis=alt.Axis(gridOpacity=0.15, domain=False, ticks=False)),
            tooltip=[alt.Tooltip("day:T", title="Day", format="%A %d %B"), alt.Tooltip("jobs:Q", title="Jobs")])
        right.altair_chart((line.mark_area(color=BLUE, opacity=0.15) + line.mark_line(color=BLUE, strokeWidth=2)
                            + line.mark_point(color=BLUE, filled=True, size=70)).properties(height=260), width="stretch")
        # roles by place: a heatmap, the count in each cell
        grid = shown.groupby(["role", "place"]).size().reset_index(name="jobs")
        heat = alt.Chart(grid, title="Roles by place").encode(
            x=alt.X("place:N", title=None, sort=alt.EncodingSortField("jobs", op="sum", order="descending"),
                    axis=alt.Axis(labelAngle=-40, labelOverlap=False, ticks=False, domain=False)),
            y=alt.Y("role:N", title=None, axis=alt.Axis(labelLimit=260, ticks=False, domain=False)),
            tooltip=[alt.Tooltip("role:N", title="Role"), alt.Tooltip("place:N", title="Where"),
                     alt.Tooltip("jobs:Q", title="Jobs")])
        left.altair_chart((heat.mark_rect(cornerRadius=3, stroke=SURFACE, strokeWidth=2).encode(
            color=alt.Color("jobs:Q", title="Jobs", scale=alt.Scale(range=HEAT), legend=None))
            + heat.mark_text(fontSize=12).encode(
                text="jobs:Q", color=alt.condition(alt.datum.jobs > grid["jobs"].max() / 2,
                                                   alt.value(HEAT_INK), alt.value(INK))))
            .properties(height=36 * grid["role"].nunique() + 40), width="stretch")
        where = bars(shown, "place", "Where")
        right.altair_chart(where, width="stretch", height=where.height + 40)
        # the most active companies and the sources: tables, with a bar inside each row
        for column, title, side in (("company", "Companies with the most jobs", left),
                                    ("source", "Where the radar found them", right)):
            top = shown[column].replace("", "No company named").value_counts().head(10).rename_axis(title).reset_index(name="Jobs")
            side.dataframe(top, hide_index=True, width="stretch", column_config={"Jobs": st.column_config.ProgressColumn(
                "Jobs", format="%d", min_value=0, max_value=int(top["Jobs"].max()) if len(top) else 1)})

with skills_tab:
    pool = shown[shown["described"]]
    demand = query("SELECT k.job_id, k.skill, s.track, cs.skill IS NOT NULL AS on_cv FROM core.job_skill k"
                   " JOIN core.skill s USING (skill) LEFT JOIN (SELECT DISTINCT skill FROM core.cv_skill) cs USING (skill)")
    track = st.segmented_control("Skills", ["All", "Data Engineering", "Generative AI"], default="All", key="track")
    asked = demand[demand["job_id"].isin(pool["job_id"])
                   & ((demand["track"] == track) if track and track != "All" else True)]
    if asked.empty:
        st.info("No job with a description matches these filters yet: the skills come from the descriptions the "
                "describe step reads.")
    else:
        top = (asked.groupby(["skill", "on_cv"]).size().reset_index(name="jobs")
               .sort_values("jobs", ascending=False).head(15))
        top["share"] = top["jobs"] / len(pool)
        top["Your CV"] = top["on_cv"].map({True: "On your CV", False: "Not on your CV yet"})
        gaps = top[~top["on_cv"]]
        k1, k2, k3 = st.columns(3, border=True)
        k1.metric("Jobs read", len(pool), help="Jobs with a description that your sidebar filters keep")
        k2.metric("Top skills on your CV", f"{int(top['on_cv'].sum())} of {len(top)}")
        k3.metric("Most asked skill you lack", gaps.iloc[0]["skill"] if len(gaps) else "None",
                  f"{gaps.iloc[0]['share']:.0%} of jobs ask for it" if len(gaps) else None, delta_color="off")
        if len(gaps):
            st.markdown("**Learn next:** " + " · ".join(f"{g.skill} ({g.share:.0%})" for g in gaps.head(3).itertuples()))
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
            + base.mark_text(align="left", dx=4, color=INK).encode(text=alt.Text("share:Q", format=".0%")))
            .properties(title="The skills these jobs ask for most", height=28 * len(top)),
            width="stretch", height=28 * len(top) + 110)  # Streamlit fits a chart to its element: give it the room
        st.caption("Each bar: of the jobs read, the share whose title or description names the skill. Blue skills "
                   "are on your CV, orange ones are not yet.")

with companies_tab:
    st.caption("Your companies, from settings.yaml (companies): add, change or remove them there and the next run "
               "updates this list. A starred company's jobs are starred in the email and the tracker. With a careers "
               "page, the next run detects its platform (Workable, Greenhouse, Lever, Ashby, Phenom, SuccessFactors, "
               "RSS, or any other page, read with Playwright) and reads its jobs every run. Sites that turn scripts "
               "away are shown as such and skipped until you open them once with scripts/open_blocked.py.")
    companies = query("SELECT company, starred, careers_url, platform, note, checked_at FROM core.company"
                      " ORDER BY company")
    st.dataframe(companies, hide_index=True, column_config={
        "starred": st.column_config.CheckboxColumn("Starred"),
        "careers_url": st.column_config.LinkColumn("Careers page"), "platform": "Read as",
        "note": "Why not read", "checked_at": st.column_config.DatetimeColumn("Checked", format="D MMM, HH:mm")})

with cv_tab:
    st.caption("Upload your CV as a PDF. The next run shows, for every job, how much of what it asks for "
               "your CV covers, and hands your newest CV to the career-ops export. It stays in your warehouse.")
    with st.form("add_cv", clear_on_submit=True):
        label = st.text_input("Label", value="AI & Data Engineer")
        pdf = st.file_uploader("CV (PDF)", type=["pdf"])
        if st.form_submit_button("Upload", type="primary"):
            if not pdf or not label.strip():
                st.error("Choose a PDF and give it a label, then upload.")
            else:
                with st.spinner("Reading your CV…"):
                    text = "\n".join(page.extract_text() or "" for page in PdfReader(pdf).pages)
                    text = " ".join(text.split())  # some PDFs come out one word per line: "power\nbi" never matches
                if not text.strip():
                    st.error("No text found in this PDF (a scanned image?): export it from Word or Google Docs as PDF.")
                else:
                    with connect() as conn:
                        conn.execute("INSERT INTO core.cv (label, filename, text) VALUES (%s, %s, %s) ON CONFLICT (label)"
                                     " DO UPDATE SET filename = EXCLUDED.filename, text = EXCLUDED.text, uploaded_at = now()",
                                     (label.strip(), pdf.name, text))
                    st.rerun()
    cvs = query("SELECT c.label, c.filename, length(c.text) AS characters, c.uploaded_at,"
                " string_agg(cs.skill, ', ' ORDER BY cs.skill) AS skills FROM core.cv c"
                " LEFT JOIN core.cv_skill cs USING (label) GROUP BY c.label ORDER BY c.uploaded_at DESC")
    st.dataframe(cvs, hide_index=True, column_config={
        "uploaded_at": st.column_config.DatetimeColumn("Uploaded", format="D MMM, HH:mm"),
        "skills": st.column_config.TextColumn("Skills found on it", help="Matched by the next run (match_skills)")})
    drop = st.multiselect("CVs to remove", cvs["label"])
    with st.popover("Remove CVs", disabled=not drop):
        st.write(f"Remove {plural(len(drop), 'CV')} for good? This cannot be undone.")
        if st.button("Remove for good", type="primary"):
            with connect() as conn:
                conn.execute("DELETE FROM core.cv WHERE label = ANY(%s)", (drop,))
            st.rerun()
