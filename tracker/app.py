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
from job_radar.config import AI_PLACES, BAYT_PLACES, FEATURES, HOME, hidden_reason  # noqa: E402
from job_radar.db import connect  # noqa: E402
from tracker.applications_tab import follow_ups  # noqa: E402
from tracker.runlog_tab import dollars  # noqa: E402

STATUSES = ["new", "saved", "applied", "interview", "offer", "rejected", "ignored"]
OPEN = ["new", "saved", "applied", "interview", "offer"]
# Apply within 48 hours of the posting: mart.job_status.fresh_level, from when the job was posted
FRESH = {1: "Within 12 hours", 2: "Within 24 hours", 3: "Over 1 day", 4: "Over 2 days", 5: "Over 3 days",
         6: "Deleted tomorrow"}
# Sort by any of these, in the order picked: ↓ is highest, newest or Z first; ↑ is lowest, oldest or A first
SORTS = {"Score": "score", "Title": "title", "Company": "company", "Where": "place", "Posted": "posted_at", "CV match": "cv_match",
         "Skills": "skill_matches", "Status": "status", "Role": "role_rank", "Target": "target_company", "CV covers": "cv_coverage",
         "Company size": "most_employees", "First seen": "first_seen", "Source": "source"}
SORT_OPTIONS = [f"{name} {arrow}" for name in SORTS for arrow in ("↓", "↑")]
DEFAULT_SORT = ["Role ↑", "Score ↓", "Posted ↓"]  # your roles in settings.yaml order (data engineering and AI first), best graded, then the newest
# the sidebar's list filters: widget key -> column
FILTERS = {"role": "role", "where": "place", "posted": "posted", "status": "status"}
# the boards dropdowns: widget key -> (label, places); picking boards keeps only their jobs in those places
BOARDS = {"egypt_board": ("Egypt boards", {HOME}), "gulf_board": ("Gulf boards", BAYT_PLACES - {HOME})}
# Small companies: the most employees a company can have to pass. A range passes by its top ("11-50" is in
# 1-50), "1,000+" never does, and a company of unknown size is left out while one is picked.
SIZES = {"1-10": 10, "1-20": 20, "1-50": 50, "1-100": 100, "1-200": 200}
# chart colors (the dataviz skill's palette, dark-surface steps): one blue for a single series; blue and
# orange for "on your CV" and "not on your CV yet"
BLUE, ORANGE = "#3987e5", "#d95926"
GRAY = "#6f7d99"  # everything that is not the point of the chart: DE and AI are blue, the rest gray


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def most_employees(size: str | None) -> float | None:
    """The top of a company size ('51-200' -> 200, '251' -> 251); '1,000+' has no top."""
    if not size:
        return None
    return float("inf") if size.endswith("+") else float(size.replace(",", "").split("-")[-1])


@st.cache_data(ttl=60)  # every click reruns this script: the same query within a minute is not asked again (query.clear() after a write)
def query(sql: str) -> pd.DataFrame:
    with connect() as conn:
        cur = conn.execute(sql)
        return pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])


st.set_page_config(page_title="Job tracker", page_icon=":material/work:", layout="wide")
st.title("Job tracker", icon=":material/radar:")
LIGHT = st.context.theme.type == "light"
INK = "#0e1630" if LIGHT else "#eef2fa"  # chart text (.streamlit/config.toml's textColor): never a series color
SURFACE = "#ffffff" if LIGHT else "#0b1124"  # the page behind the charts (its backgroundColor), for the gaps between cells
# one blue ramp: the freshest jobs brightest on a dark page (darkest on a light one), then orange for the ones
# deleted tomorrow; the heatmap from faint to strong
HEAT = ["#eef4fc", "#1c5cab"] if LIGHT else ["#1a2738", "#86b6ef"]
HEAT_INK = "#ffffff" if LIGHT else "#0d1b2a"  # the count on the strongest cells

jobs = query("SELECT job_id, role_rank, fresh_level, posted_at, score, skill_matches, cv_coverage, cv_similarity, target_company,"
             " title, company, company_size, role, place, source, status, note, first_seen, job_url, skills_matched, described,"
             " restriction, grade_reason FROM mart.job_status")
jobs["posted"] = jobs["fresh_level"].map(FRESH)
jobs["score"] = pd.to_numeric(jobs["score"])
# The switches (settings.yaml features) are read when the tracker starts: restart it to apply a change (docker restart job-radar-tracker-1).
# A job is hidden as they stand: a restriction with eligibility, a score of 3 or lower with auto_hide and grade.
HIDES = FEATURES["eligibility"] or (FEATURES["auto_hide"] and FEATURES["grade"])
# which jobs those are, naming only the reasons whose switches are on
HIDDEN_JOBS = " or ".join(
    [*(["you cannot take from Egypt"] if FEATURES["eligibility"] else []),
     *(["that Claude scored 3 or lower"] if FEATURES["auto_hide"] and FEATURES["grade"] else [])])
jobs["hidden_reason"] = [hidden_reason(*row) for row in zip(jobs["status"], jobs["restriction"], jobs["score"])]
if not FEATURES["grade"]:  # off: no score sorts or shows
    jobs["score"], jobs["grade_reason"] = float("nan"), None
jobs["cv_coverage"] = pd.to_numeric(jobs["cv_coverage"])
jobs["cv_match"] = pd.to_numeric(jobs["cv_similarity"]) * 100  # how close the job's text is to your nearest CV (embed)
jobs["why"] = jobs["hidden_reason"].fillna(jobs["grade_reason"])  # why Claude or a rule hid it, else Claude's line
jobs["most_employees"] = pd.to_numeric(jobs["company_size"].map(most_employees))

OPTIONS = {"role": sorted(jobs["role"].unique()), "where": sorted(jobs["place"].unique()),
           "posted": list(FRESH.values()), "status": STATUSES,
           **{name: sorted(jobs.loc[jobs["place"].isin(places), "source"].unique()) for name, (_, places) in BOARDS.items()}}
DEFAULTS = {"role": [], "where": [], "posted": [], "status": [s for s in OPEN if s != "applied"], "egypt_board": [], "gulf_board": [], "size": None, "skills": 0, "q": "",
            "sort": DEFAULT_SORT, "hidden": False, "hide_applied": True}
# The filters live in the address, so a reload or a bookmark keeps them. Each one is set when missing, so a
# session from before a filter existed (or one whose widget a failed run dropped) gets it too.
url = st.query_params
skills_in_url = url.get("skills", "0")
for name, value in {
        **{name: [o for o in url.get_all(name) if o in OPTIONS[name]] or list(DEFAULTS[name]) for name in [*FILTERS, *BOARDS]},
        "skills": min(int(skills_in_url), 10) if skills_in_url.isdigit() else 0,
        "size": url.get("size") if url.get("size") in SIZES else None,
        "q": url.get("q", ""),
        "sort": [o for o in url.get_all("sort") if o in SORT_OPTIONS] or list(DEFAULT_SORT),
        "hidden": False, "hide_applied": True}.items():
    if name not in st.session_state:
        st.session_state[name] = value


def reset() -> None:
    st.session_state.update({name: list(v) if isinstance(v, list) else v for name, v in DEFAULTS.items()})


def passing(skip: str = "") -> pd.Series:
    """The jobs every filter keeps, leaving out the filter named skip (for that filter's counts)."""
    f = st.session_state
    keep = jobs["skill_matches"] >= f.skills
    if not f.hidden:  # hidden_reason is empty for every job while no switch hides
        keep &= jobs["hidden_reason"].isna()
    if f.hide_applied:
        keep &= jobs["status"] != "applied"
    for name, column in FILTERS.items():
        if f[name] and name != skip:
            keep &= jobs[column].isin(f[name])
    for name, (_, places) in BOARDS.items():
        if f[name] and name != skip:
            keep &= ~jobs["place"].isin(places) | jobs["source"].isin(f[name])
    if f.size and skip != "size":
        keep &= jobs["most_employees"] <= SIZES[f.size]
    if f.q:
        keep &= (jobs["title"] + " " + jobs["company"]).str.contains(f.q, case=False, regex=False)
    return keep


def option_counts(name: str) -> pd.Series:
    """How many jobs each option of a filter gives with the other filters as they are, in option order."""
    if name in BOARDS:
        return (jobs.loc[passing(skip=name) & jobs["place"].isin(BOARDS[name][1]), "source"]
                .value_counts().reindex(OPTIONS[name], fill_value=0))
    return jobs.loc[passing(skip=name), FILTERS[name]].value_counts().reindex(OPTIONS[name], fill_value=0)


def counts_line(name: str) -> None:
    """The count of each option under its filter, the other filters as they are. Never in the option labels: a label
    that changes makes Streamlit treat the filter as a new widget and clear what you picked."""
    n = option_counts(name)
    st.caption(" · ".join(f"{option} **{count}**" for option, count in n[n > 0].items()) or "no jobs")


with st.sidebar:
    # the filters used every day first; the rest folded under More filters (open while one of them is set)
    text = st.text_input("Search title or company", key="q", icon=":material/search:")
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
    st.toggle("Hide applied jobs", key="hide_applied", help="Keep the jobs you set to applied out of the Jobs table")
    more = any(st.session_state[name] for name in BOARDS) or st.session_state.size or st.session_state.skills or st.session_state.hidden or st.session_state.sort != DEFAULT_SORT
    with st.expander("More filters", icon=":material/tune:", expanded=bool(more)):
        sort = st.multiselect("Sort by", SORT_OPTIONS, key="sort",
                              help="First pick first: ↓ is highest, newest or Z first, ↑ lowest, oldest or A first "
                                   "(Role ↑ is your order in settings.yaml). A column picked twice counts once.")
        boards = {}
        for name, (label, board_places) in BOARDS.items():
            boards[name] = st.multiselect(label, OPTIONS[name], key=name,
                                          help=f"Only the jobs from these boards in {', '.join(sorted(board_places))}")
            counts_line(name)
        size = st.segmented_control("Company size", list(SIZES), key="size",
                                    help="Employees. A range counts by its top: 11-50 is in 1-50. Jobs whose company "
                                         "size no site gives are hidden while one is picked.")
        min_skills = st.slider("At least this many of your skills", 0, 10, key="skills")
        if HIDES:
            st.toggle("Show hidden jobs", key="hidden",
                      help=f"Jobs {HIDDEN_JOBS}; the Why column says which")
    st.button("Reset filters", icon=":material/restart_alt:", on_click=reset, width="stretch")
st.query_params.from_dict({k: v for k, v in {"role": roles, "where": places, "posted": posted, "status": statuses,
                                             "size": size, "skills": min_skills, "q": text, "sort": sort, **boards}.items() if v})

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
shown = shown.iloc[(shown["company"] == "").to_numpy().argsort(kind="stable")]  # jobs with no company name go last, the order kept

# the jobs waiting on you; applied, interviews and offers are counted once, from your mailbox, in the Applications tab
counts = shown["status"].value_counts()  # the same jobs as Showing, so the four numbers add up
fresh_now = int((shown["fresh_level"] <= 3).sum())
with st.container(horizontal=True):  # four numbers: one row on a desktop, two by two on a phone
    st.metric("Showing", len(shown), border=True, width=170, help=f"Jobs your filters keep, of {len(jobs)} the radar holds")
    st.metric("Apply now", fresh_now, border=True, width=170, help="Within 48 hours of the posting: the ones to apply to first")
    st.metric("New", int(counts.get("new", 0)), border=True, width=170, help="Of those, the ones you have not set a status for")
    st.metric("Saved", int(counts.get("saved", 0)), border=True, width=170,
              help="Of those, the ones you saved, including the drafts Claude prepared (Ready tab)")


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


# the four used every day, then More: Skills, Companies, Contacts, CV and Last run behind one picker
jobs_tab, ready_tab, applications_tab, dashboard_tab, more_tab = st.tabs(
    [":material/work: Jobs", ":material/task_alt: Ready", ":material/mail: Applications", ":material/monitoring: Dashboard",
     ":material/more_horiz: More"])
PRIORITY_ROLES = 3  # roles 1-3 in settings.yaml (AI & Data Engineer, Data Engineer, AI Engineer) lead the lists
ESSENTIALS = {"score", "title", "company", "place", "posted", "skill_matches", "status", "job_url"}
MORE = ["Skills", "Companies", "Contacts", "CV", "Last run"]
with more_tab:
    section = st.segmented_control("More", MORE, default="Skills", label_visibility="collapsed")

with ready_tab:
    from tracker.ready_tab import render as render_ready  # noqa: E402  (the drafts for your best-graded jobs)
    render_ready(query)

with applications_tab:
    from tracker.applications_tab import render  # noqa: E402  (your applications, from your mailbox)
    render(query)

with jobs_tab:
    if HIDES and not st.session_state.hidden:
        st.caption(f"{int(jobs['hidden_reason'].notna().sum())} more jobs are hidden: ones {HIDDEN_JOBS}. "
                   "Turn on Show hidden jobs under More filters to see them.")
    if shown.empty:
        empty = [o for name in [*FILTERS, *BOARDS] for o in st.session_state[name] if option_counts(name)[o] == 0]
        st.info("No jobs match these filters"
                + (f": {', '.join(empty)} {'has' if len(empty) == 1 else 'have'} no jobs with the other filters"
                   if empty else "")
                + (" (Hide applied jobs is on in the sidebar)" if st.session_state.hide_applied and "applied" in st.session_state.status else "")
                + ". Remove one, or press Reset filters in the sidebar.")
    else:
        columns = st.segmented_control("Columns", ["Essentials", "All"], default="Essentials", label_visibility="collapsed",
                                       help="Essentials: the columns you decide on. All: the scores, the reasons and the rest.")
        table = shown.assign(score=shown["score"].map(lambda v: "n/a" if v != v else str(int(v))),
                             company=[c or f"{s} (company hidden)" for c, s in zip(shown["company"], shown["source"])])
        edited = st.data_editor(
            table, key="jobs", hide_index=True, height=620,
            # what you decide on first, then the rest
            column_order=[c for c in ["score", "title", "company", "role", "place", "posted", "skill_matches", "why", "cv_match", "status",
                                      "note", "job_url", "target_company", "cv_coverage", "skills_matched", "company_size",
                                      "posted_at", "first_seen", "source"]
                          # Claude's columns only with features.grade, the CV match only with features.embeddings
                          if (FEATURES["grade"] or c not in ("score", "why")) and (FEATURES["embeddings"] or c != "cv_match")
                          and (columns == "All" or c in ESSENTIALS)],
            disabled=[c for c in shown.columns if c not in ("status", "note")],
            column_config={
                "score": st.column_config.TextColumn("Score", width="small", help="Claude's grade of the job against your CVs, 0-10; n/a when not graded"),
                "title": st.column_config.TextColumn("Title", width=300),
                "company": st.column_config.TextColumn("Company", width=130),
                "place": st.column_config.TextColumn("Where", width="small"),
                "posted": st.column_config.TextColumn("Posted", width=110, help="Apply within 48 hours of the posting"),
                "posted_at": st.column_config.DatetimeColumn("Posted at", format="D MMM, h:mma"),
                "why": st.column_config.TextColumn("Why", width="medium", help="Why the job is hidden, else Claude's reason for its score"),
                "skill_matches": st.column_config.NumberColumn("Skills", width="small", help="How many of your skills the job asks for"),
                "cv_coverage": st.column_config.ProgressColumn("CV covers", min_value=0, max_value=100, format="%d%%",
                                                               help="How much of what the job asks for your best CV shows"),
                "cv_match": st.column_config.ProgressColumn("CV match", min_value=0, max_value=100, format="%d%%",
                                                            help="How close the job's text is to your nearest CV (embeddings)"),
                "target_company": st.column_config.CheckboxColumn("Target"),
                "company_size": st.column_config.TextColumn(
                    "Company size", help="Employees, when a job board gives it (Indeed, freehire)"),
                "status": st.column_config.SelectboxColumn("Status", options=STATUSES, required=True),
                "note": st.column_config.TextColumn("Note", width="medium"),
                "role": st.column_config.TextColumn("Role", width=130), "source": "Source",
                "job_url": st.column_config.LinkColumn("Link", display_text="Open", width="small"),
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
            query.clear()
            st.rerun()

with dashboard_tab:
    if shown.empty:
        st.info("No jobs match these filters. Remove one, or press Reset filters in the sidebar.")
    else:
        # 1. what to do now: data engineering and AI jobs first (roles 1-3 in settings.yaml), the freshest and best graded on top
        st.subheader("Apply next")
        st.caption("Posted in the last 48 hours: data engineering and AI roles first, then the best graded.")
        best = (shown[shown["fresh_level"] <= 3]
                .assign(priority=lambda d: d["role_rank"] > PRIORITY_ROLES, unnamed=lambda d: d["company"] == "")
                .sort_values(["priority", "unnamed", "score", "skill_matches"], ascending=[True, True, False, False],
                             na_position="last").head(8))
        if best.empty:
            st.info("Nothing posted in the last 48 hours matches these filters.")
        else:
            st.dataframe(best[[c for c in ["score", "title", "company", "role", "place", "posted", "why", "job_url"]
                               if FEATURES["grade"] or c not in ("score", "why")]], hide_index=True, column_config={
                "score": st.column_config.ProgressColumn("Score", min_value=0, max_value=10, format="%d", width="small"),
                "title": st.column_config.TextColumn("Title", width=320), "company": "Company", "role": "Role", "place": "Where",
                "posted": "Posted", "why": st.column_config.TextColumn("Why", width="large"),
                "job_url": st.column_config.LinkColumn("Link", display_text="Open", width="small")})

        # 2. your applications: the stages from your mailbox, and what is due
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
                           + stages.mark_text(align="left", dx=4, color=INK).encode(text="Companies:Q"))
                          .properties(title="Companies by stage, from your mailbox", height=36 * len(funnel)), width="stretch")
        applied = int(funnel.loc[funnel["Stage"] == "Applied", "Companies"].iloc[0])
        if applied:
            left.caption(" · ".join(f"{r.Stage} {r.Companies / applied:.0%}" for r in funnel.itertuples() if r.Stage != "Applied")
                         + " of the companies you applied to")
        right.metric("Applied in the tracker", int((jobs["status"] == "applied").sum()), border=True,
                     help="Jobs you set to applied, with or without a confirmation email")
        right.metric("Follow-ups due today", due, border=True, help="Applied 7 or 14 days ago with no answer yet (Applications tab)")

        # 3. the market, read for data engineering and AI first: blue is DE and AI, gray is everything else
        st.subheader("The market")
        prio = shown["role_rank"] <= PRIORITY_ROLES
        de_ai = shown[prio]
        today = pd.Timestamp.now(tz="Africa/Cairo").date()
        per_day = de_ai.groupby("first_seen").size()
        earlier = per_day[per_day.index < today]
        new_today = int(per_day.get(today, 0))
        coverage_now = de_ai["cv_coverage"].dropna()
        with st.container(horizontal=True):
            st.metric("DE and AI jobs", len(de_ai), border=True, width=170,
                      delta=f"{len(de_ai) / len(shown):.0%} of {len(shown)}", delta_color="off",
                      help="Jobs in your first three roles in settings.yaml, of the jobs your filters keep")
            st.metric("New today", new_today, border=True, width=170,
                      delta=f"{new_today - earlier.mean():+.0f} vs a usual day" if len(earlier) else None,
                      help="DE and AI jobs first found today, against the average of the earlier days the radar kept")
            st.metric("Remote", f"{(de_ai['place'] == 'Remote').mean():.0%}" if len(de_ai) else "n/a", border=True, width=170,
                      help="Share of the DE and AI jobs open to anywhere, or to Egypt")
            st.metric("Median CV coverage", f"{coverage_now.median():.0f}%" if len(coverage_now) else "n/a", border=True, width=200,
                      help="Of the skills a DE or AI job asks for, how many your best CV shows; the middle job (jobs with a description)")
        left, right = st.columns(2, gap="large")
        # the days the radar ran: DE and AI in blue on top of the rest in gray
        days = (shown.assign(group=prio.map({True: "DE and AI", False: "Other roles"})).groupby(["first_seen", "group"]).size()
                .reset_index(name="jobs"))
        days["day"] = pd.to_datetime(days["first_seen"])
        left.altair_chart(alt.Chart(days, title="New jobs a day, DE and AI in blue").mark_bar(cornerRadiusEnd=3, size=26).encode(
            x=alt.X("day:T", title=None, axis=alt.Axis(format="%a %d", tickCount="day", grid=False)),
            y=alt.Y("jobs:Q", title=None, axis=alt.Axis(gridOpacity=0.15, domain=False, ticks=False)),
            color=alt.Color("group:N", title=None, sort=["DE and AI", "Other roles"], legend=alt.Legend(orient="top"),
                            scale=alt.Scale(domain=["DE and AI", "Other roles"], range=[BLUE, GRAY])),
            order=alt.Order("group:N", sort="descending"),
            tooltip=[alt.Tooltip("day:T", title="Day", format="%A %d %B"), alt.Tooltip("group:N", title="Roles"),
                     alt.Tooltip("jobs:Q", title="Jobs")]).properties(height=240), width="stretch")
        # where the jobs are: the DE and AI ones first, the rest beside them
        where = (shown.assign(group=prio.map({True: "DE and AI", False: "Other roles"})).groupby(["place", "group"]).size()
                 .reset_index(name="jobs"))
        order = list(where[where["group"] == "DE and AI"].sort_values("jobs", ascending=False)["place"])
        order += [p for p in where["place"].unique() if p not in order]
        right.altair_chart(alt.Chart(where, title="Where the jobs are, DE and AI in blue").mark_bar(cornerRadiusEnd=3, height=18).encode(
            y=alt.Y("place:N", sort=order, title=None, axis=alt.Axis(labelLimit=200, ticks=False, domain=False)),
            x=alt.X("jobs:Q", title=None, axis=alt.Axis(gridOpacity=0.15, domain=False, ticks=False)),
            color=alt.Color("group:N", title=None, sort=["DE and AI", "Other roles"], legend=alt.Legend(orient="top"),
                            scale=alt.Scale(domain=["DE and AI", "Other roles"], range=[BLUE, GRAY])),
            order=alt.Order("group:N", sort="descending"),
            tooltip=[alt.Tooltip("place:N", title="Where"), alt.Tooltip("group:N", title="Roles"),
                     alt.Tooltip("jobs:Q", title="Jobs")]).properties(height=28 * len(order) + 40), width="stretch")
        if len(de_ai):
            # who is hiring for DE and AI: a star marks your target companies
            hiring = (de_ai[de_ai["company"] != ""].groupby("company").agg(jobs=("job_id", "size"), target=("target_company", "max"))
                      .sort_values("jobs", ascending=False).head(10).reset_index())
            hiring["label"] = hiring["company"] + hiring["target"].map({True: " ★", False: ""})
            hire = alt.Chart(hiring).encode(
                y=alt.Y("label:N", sort=list(hiring["label"]), title=None, axis=alt.Axis(labelLimit=240, ticks=False, domain=False)),
                x=alt.X("jobs:Q", title=None, axis=None, scale=alt.Scale(domain=[0, hiring["jobs"].max() * 1.15])),
                tooltip=[alt.Tooltip("company:N", title="Company"), alt.Tooltip("jobs:Q", title="DE and AI jobs")])
            left.altair_chart((hire.mark_bar(color=BLUE, cornerRadiusEnd=4, height=18)
                               + hire.mark_text(align="left", dx=4, color=INK).encode(text="jobs:Q"))
                              .properties(title="Who is hiring for DE and AI (★ your target companies)", height=28 * len(hiring)),
                              width="stretch")
            # the boards that deliver them: their share of DE and AI jobs and the middle score
            boards_tbl = (shown[shown["source"] != ""].assign(de_ai=prio).groupby("source")
                          .agg(jobs=("job_id", "size"), de_ai=("de_ai", "sum"), score=("score", "median")).reset_index())
            boards_tbl["share"] = 100 * boards_tbl["de_ai"] / boards_tbl["jobs"]
            boards_tbl = boards_tbl.sort_values(["de_ai", "jobs"], ascending=False).head(8)
            right.markdown("**Which boards deliver DE and AI jobs**")
            right.dataframe(boards_tbl, hide_index=True, width="stretch", column_config={
                "source": "Board", "jobs": st.column_config.NumberColumn("Jobs", format="%d"),
                "de_ai": st.column_config.NumberColumn("DE and AI", format="%d"),
                "share": st.column_config.ProgressColumn("DE and AI share", min_value=0, max_value=100, format="%d%%"),
                "score": st.column_config.NumberColumn("Median score", format="%.0f",
                                                       help="Claude's middle score of the graded jobs; empty when none is graded")})
        if len(coverage_now):
            # how well your CV covers them: the spread, not only the middle
            q1, q3 = coverage_now.quantile([0.25, 0.75])
            spread = alt.Chart(pd.DataFrame({"coverage": coverage_now}), title=(
                f"Your CV covers a median {coverage_now.median():.0f}% of what DE and AI jobs ask "
                f"(middle half: {q1:.0f}-{q3:.0f}%)")).encode(
                x=alt.X("coverage:Q", bin=alt.Bin(step=10, extent=[0, 100]), title="Share of the skills a job asks for that your CV shows",
                        axis=alt.Axis(grid=False)),
                y=alt.Y("count():Q", title=None, axis=alt.Axis(gridOpacity=0.15, domain=False, ticks=False)))
            left.altair_chart((spread.mark_bar(color=BLUE, cornerRadiusEnd=3)
                               + alt.Chart(pd.DataFrame({"m": [coverage_now.median()]})).mark_rule(
                                   color=INK, strokeDash=[4, 3], strokeWidth=2).encode(x="m:Q"))
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
                                + skills.mark_text(align="left", dx=4, color=INK).encode(text=alt.Text("share:Q", format=".0%")))
                               .properties(title=f"Learn next: asked by DE and AI jobs, not on your CV ({len(read)} read)",
                                           height=28 * len(gap)), width="stretch")

        # 4. the pipeline: did the last run work, and what it cost
        st.subheader("The pipeline")
        runs = query("SELECT DISTINCT ON (step) step, status, started_at FROM core.step_run ORDER BY step, started_at DESC")
        if runs.empty:
            st.caption("No run logged yet: the next run fills this.")
        else:
            month = query("SELECT sum(cost_usd) AS cost_usd FROM core.step_run"
                          " WHERE started_at >= date_trunc('month', now() AT TIME ZONE 'Africa/Cairo')")
            ran = runs["started_at"].max().tz_convert("Africa/Cairo")
            with st.container(horizontal=True):
                st.metric("Last run", ran.strftime("%a %d %b, %I:%M%p").replace(" 0", " "), border=True, width=210)
                st.metric("Steps ok", f"{int((runs['status'] == 'ok').sum())} of {len(runs)}", border=True, width=170,
                          help="The latest run of each step. Warnings and failures are on the Last run page under More.")
                st.metric("Warnings and failures", int(runs["status"].isin(["warn", "failed"]).sum()), border=True, width=210)
                st.metric("Claude this month", dollars(month["cost_usd"].iloc[0]), border=True, width=190)

with more_tab:
    if section == "Skills":
        pool = shown[shown["described"]]
        demand = query("SELECT k.job_id, k.skill, s.track, cs.skill IS NOT NULL AS on_cv FROM core.job_skill k"
                       " JOIN core.skill s USING (skill) LEFT JOIN (SELECT DISTINCT skill FROM core.cv_skill) cs USING (skill)")
        track = st.segmented_control("Skills", ["All", "Data Engineering", "BI & Analytics", "Generative AI"], default="All", key="track")
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
            with st.container(horizontal=True):
                st.metric("Jobs read", len(pool), border=True, help="Jobs with a description that your sidebar filters keep")
                st.metric("Top skills on your CV", f"{int(top['on_cv'].sum())} of {len(top)}", border=True)
                st.metric("Most asked skill you lack", gaps.iloc[0]["skill"] if len(gaps) else "None", border=True,
                          delta=f"{gaps.iloc[0]['share']:.0%} of jobs ask for it" if len(gaps) else None, delta_color="off")
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

    if section == "Companies":
        st.caption("Your companies, from settings.yaml (companies): add, change or remove them there and the next run "
                   "updates this list. A starred company's jobs are starred in the email and the tracker. With a careers "
                   "page, the next run detects its platform (Workable, Greenhouse, Lever, Ashby, Phenom, SuccessFactors, "
                   "RSS, or any other page, read with Playwright) and reads its jobs every run. Sites that turn scripts "
                   "away are shown as such and skipped until you open them once with scripts/open_blocked.py.")
        companies = query("SELECT company, starred, careers_url, platform, note, checked_at FROM core.company"
                          " ORDER BY starred DESC, company")
        st.dataframe(companies, hide_index=True, column_config={
            "company": "Company", "starred": st.column_config.CheckboxColumn("Starred"),
            "careers_url": st.column_config.LinkColumn("Careers page"), "platform": "Read as",
            "note": "Why not read", "checked_at": st.column_config.DatetimeColumn("Checked", format="D MMM, HH:mm")})

    if section == "Contacts":
        from tracker.contacts_tab import render as render_contacts  # noqa: E402  (data/contacts.csv, gitignored)
        render_contacts()

    if section == "CV":
        st.caption("Upload your CV as a PDF. The next run shows, for every job, how much of what it asks for "
                   "your CV covers, and hands your newest CV to the career-ops export. It stays in your warehouse.")
        with st.form("add_cv", clear_on_submit=True):
            label = st.text_input("Label", value="Data Engineer & BI Developer")
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
                        query.clear()
                        st.rerun()
        cvs = query("SELECT c.label, c.filename, length(c.text) AS characters, c.uploaded_at,"
                    " string_agg(cs.skill, ', ' ORDER BY cs.skill) AS skills FROM core.cv c"
                    " LEFT JOIN core.cv_skill cs USING (label) GROUP BY c.label ORDER BY c.uploaded_at DESC")
        st.dataframe(cvs, hide_index=True, column_order=["label", "uploaded_at", "skills", "filename", "characters"], column_config={
            "label": "CV", "filename": "File", "characters": "Characters",
            "uploaded_at": st.column_config.DatetimeColumn("Uploaded", format="D MMM, HH:mm"),
            "skills": st.column_config.TextColumn("Skills found on it", help="Matched by the next run (match_skills)")})
        drop = st.multiselect("CVs to remove", cvs["label"])
        with st.popover("Remove CVs", disabled=not drop):
            st.write(f"Remove {plural(len(drop), 'CV')} for good? This cannot be undone.")
            if st.button("Remove for good", type="primary"):
                with connect() as conn:
                    conn.execute("DELETE FROM core.cv WHERE label = ANY(%s)", (drop,))
                query.clear()
                st.rerun()

    if section == "Last run":
        from tracker.runlog_tab import render as render_runlog  # noqa: E402  (core.step_run, written by runlog.py)
        render_runlog(query)
