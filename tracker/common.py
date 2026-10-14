"""What the tracker's pages share: the warehouse query, the jobs, the sidebar filters and the chart colors."""

import pandas as pd
import streamlit as st

from job_radar.config import BAYT_PLACES, FEATURES, HOME, hidden_reason
from job_radar.db import connect

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
PRIORITY_ROLES = 3  # roles 1-3 in settings.yaml (AI & Data Engineer, Data Engineer, AI Engineer) lead the lists
# the sidebar's list filters: widget key -> column
FILTERS = {"role": "role", "where": "place", "posted": "posted", "status": "status"}
# the boards dropdowns: widget key -> (label, places); picking boards keeps only their jobs in those places
BOARDS = {"egypt_board": ("Egypt boards", {HOME}), "gulf_board": ("Gulf boards", BAYT_PLACES - {HOME})}
# Small companies: the most employees a company can have to pass. A range passes by its top ("11-50" is in
# 1-50), "1,000+" never does, and a company of unknown size is left out while one is picked.
SIZES = {"1-10": 10, "1-20": 20, "1-50": 50, "1-100": 100, "1-200": 200}
DEFAULTS = {"role": [], "where": [], "posted": [], "status": [s for s in OPEN if s != "applied"], "egypt_board": [],
            "gulf_board": [], "size": None, "skills": 0, "q": "", "sort": DEFAULT_SORT, "hidden": False, "hide_applied": True}
# chart colors (the dataviz skill's palette, validated on the dark surface): one blue for the point of a chart, gray for
# the rest, orange only for "not on your CV yet"
BLUE, ORANGE, GRAY = "#3987e5", "#d95926", "#6f7d99"
# A job is hidden as the switches stand (settings.yaml features, read when the tracker starts: restart it to apply a
# change): a restriction with eligibility, a score of 3 or lower with auto_hide and grade.
HIDES = FEATURES["eligibility"] or (FEATURES["auto_hide"] and FEATURES["grade"])
HIDDEN_JOBS = " or ".join(
    [*(["you cannot take from Egypt"] if FEATURES["eligibility"] else []),
     *(["that Claude scored 3 or lower"] if FEATURES["auto_hide"] and FEATURES["grade"] else [])])


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def most_employees(size: str | None) -> float | None:
    """The top of a company size ('51-200' -> 200, '251' -> 251); '1,000+' has no top."""
    if not size:
        return None
    return float("inf") if size.endswith("+") else float(size.replace(",", "").split("-")[-1])


@st.cache_data(ttl=60)  # every click reruns the page: the same query within a minute is not asked again (query.clear() after a write)
def query(sql: str) -> pd.DataFrame:
    with connect() as conn:
        cur = conn.execute(sql)
        return pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])


def colors() -> dict:
    """The chart text and page colors of the theme being shown (.streamlit/config.toml): read on every run, since the
    viewer can switch between light and dark."""
    light = st.context.theme.type == "light"
    return {"ink": "#0e1630" if light else "#eef2fa", "surface": "#ffffff" if light else "#0b1124",
            "heat": ["#eef4fc", "#1c5cab"] if light else ["#1a2738", "#86b6ef"], "heat_ink": "#ffffff" if light else "#0d1b2a"}


def load_jobs() -> pd.DataFrame:
    jobs = query("SELECT job_id, role_rank, fresh_level, posted_at, score, skill_matches, cv_coverage, cv_similarity, target_company,"
                 " title, company, company_size, role, place, source, status, note, first_seen, job_url, skills_matched, described,"
                 " restriction, grade_reason, years_required, skills_missing FROM mart.job_status")
    jobs["posted"] = jobs["fresh_level"].map(FRESH)
    jobs["score"] = pd.to_numeric(jobs["score"])
    jobs["hidden_reason"] = [hidden_reason(*row) for row in zip(jobs["status"], jobs["restriction"], jobs["score"])]
    if not FEATURES["grade"]:  # off: no score sorts or shows
        jobs["score"], jobs["grade_reason"] = float("nan"), None
    jobs["cv_coverage"] = pd.to_numeric(jobs["cv_coverage"])
    jobs["cv_match"] = pd.to_numeric(jobs["cv_similarity"]) * 100  # how close the job's text is to your nearest CV (embed)
    jobs["why"] = jobs["hidden_reason"].fillna(jobs["grade_reason"])  # why Claude or a rule hid it, else Claude's line
    jobs["most_employees"] = pd.to_numeric(jobs["company_size"].map(most_employees))
    return jobs


def keep_filters() -> None:
    """Streamlit forgets a widget's value on a page that does not draw it: write each filter back, so the filters
    survive a visit to Ready or Applications. Called by app.py before every page."""
    for name in DEFAULTS:
        if name in st.session_state:
            st.session_state[name] = st.session_state[name]


def filtered(jobs: pd.DataFrame) -> pd.DataFrame:
    """Draw the filters in the sidebar and return the jobs they keep, sorted. The filters live in the address too,
    so a reload or a bookmark keeps them."""
    options = {"role": sorted(jobs["role"].unique()), "where": sorted(jobs["place"].unique()),
               "posted": list(FRESH.values()), "status": STATUSES,
               **{name: sorted(jobs.loc[jobs["place"].isin(places), "source"].unique()) for name, (_, places) in BOARDS.items()}}
    url = st.query_params
    skills_in_url = url.get("skills", "0")
    for name, value in {
            **{name: [o for o in url.get_all(name) if o in options[name]] or list(DEFAULTS[name]) for name in [*FILTERS, *BOARDS]},
            "skills": min(int(skills_in_url), 10) if skills_in_url.isdigit() else 0,
            "size": url.get("size") if url.get("size") in SIZES else None,
            "q": url.get("q", ""),
            "sort": [o for o in url.get_all("sort") if o in SORT_OPTIONS] or list(DEFAULT_SORT),
            "hidden": False, "hide_applied": True}.items():
        if name not in st.session_state:
            st.session_state[name] = value
    f = st.session_state

    def passing(skip: str = "") -> pd.Series:
        """The jobs every filter keeps, leaving out the filter named skip (for that filter's counts)."""
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

    def counts(name: str) -> None:
        """The count of each option under its filter, the other filters as they are. Never in the option labels: a
        label that changes makes Streamlit treat the filter as a new widget and clear what you picked."""
        pool = passing(skip=name)
        n = (jobs.loc[pool & jobs["place"].isin(BOARDS[name][1]), "source"] if name in BOARDS else jobs.loc[pool, FILTERS[name]])
        n = n.value_counts().reindex(options[name], fill_value=0)
        st.caption(" · ".join(f"{option} **{count}**" for option, count in n[n > 0].items()) or "no jobs")

    with st.sidebar:
        st.text_input("Search title or company", key="q", icon=":material/search:")
        st.multiselect("Role", options["role"], key="role")
        counts("role")
        st.multiselect("Where", options["where"], key="where")
        counts("where")
        st.multiselect("Posted", options["posted"], key="posted",
                       help="Apply within 48 hours of the posting. When a site gives no time, the time the radar first "
                            "found the job counts (or the posting date, when that is older).")
        counts("posted")
        st.multiselect("Status", STATUSES, key="status")
        counts("status")
        st.toggle("Hide applied jobs", key="hide_applied", help="Keep the jobs you set to applied out of the lists")
        more = any(f[name] for name in BOARDS) or f.size or f.skills or f.hidden or f.sort != DEFAULT_SORT
        with st.expander("More filters", icon=":material/tune:", expanded=bool(more)):
            st.multiselect("Sort by", SORT_OPTIONS, key="sort",
                           help="First pick first: ↓ is highest, newest or Z first, ↑ lowest, oldest or A first "
                                "(Role ↑ is your order in settings.yaml). A column picked twice counts once.")
            for name, (label, board_places) in BOARDS.items():
                st.multiselect(label, options[name], key=name,
                               help=f"Only the jobs from these boards in {', '.join(sorted(board_places))}")
                counts(name)
            st.segmented_control("Company size", list(SIZES), key="size",
                                 help="Employees. A range counts by its top: 11-50 is in 1-50. Jobs whose company size "
                                      "no site gives are hidden while one is picked.")
            st.slider("At least this many of your skills", 0, 10, key="skills")
            if HIDES:
                st.toggle("Show hidden jobs", key="hidden", help=f"Jobs {HIDDEN_JOBS}; the Why column says which")
        st.button("Reset filters", icon=":material/restart_alt:", width="stretch",
                  on_click=lambda: f.update({k: list(v) if isinstance(v, list) else v for k, v in DEFAULTS.items()}))
    st.query_params.from_dict({k: f[k] for k in [*FILTERS, *BOARDS, "size", "skills", "q", "sort"] if f[k]})

    shown = jobs[passing()]
    by, ascending = [], []
    for choice in f.sort:
        name, arrow = choice.rsplit(" ", 1)
        if SORTS[name] not in by:
            by.append(SORTS[name])
            ascending.append(arrow == "↑")
    if by:
        shown = shown.sort_values(by, ascending=ascending, na_position="last", kind="stable",
                                  key=lambda c: c.str.lower() if pd.api.types.is_string_dtype(c) else c)
    return shown.iloc[(shown["company"] == "").to_numpy().argsort(kind="stable")]  # no company name goes last, the order kept


def numbers(jobs: pd.DataFrame, shown: pd.DataFrame) -> None:
    """The four numbers at the top of the job pages: the same jobs throughout, so they add up."""
    counts = shown["status"].value_counts()
    with st.container(horizontal=True):  # one row on a desktop, two by two on a phone
        st.metric("Showing", len(shown), border=True, width=170, help=f"Jobs your filters keep, of {len(jobs)} the radar holds")
        st.metric("Apply now", int((shown["fresh_level"] <= 3).sum()), border=True, width=170,
                  help="Within 48 hours of the posting: the ones to apply to first")
        st.metric("New", int(counts.get("new", 0)), border=True, width=170, help="Of those, the ones you have not set a status for")
        st.metric("Saved", int(counts.get("saved", 0)), border=True, width=170,
                  help="Of those, the ones you saved, including the drafts Claude prepared (Ready)")


def nothing_matches() -> None:
    st.info("No jobs match these filters. Remove one, or press Reset filters in the sidebar.", icon=":material/filter_alt_off:")
