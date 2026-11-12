"""Jobs: every job your filters keep, with your status and note for each."""

import streamlit as st

from job_radar.config import FEATURES
from job_radar.db import connect
from tracker.common import (HIDDEN_JOBS, HIDES, STATUSES, filtered, load_jobs, nothing_matches, numbers, plural, query,
                            save_edits, table_key)
from tracker.decide import apply_decisions

ESSENTIALS = {"score", "title", "company", "place", "source", "posted_ago", "skill_matches", "years_required", "status", "job_url"}

st.title("Jobs", icon=":material/work:")
jobs = load_jobs()
shown = filtered(jobs)
numbers(jobs, shown)
if HIDES and not st.session_state.hidden:
    st.caption(f"{int(jobs['hidden_reason'].notna().sum())} more jobs are hidden: ones {HIDDEN_JOBS}. "
               "Turn on Show hidden jobs under More filters to see them.")
if shown.empty:
    nothing_matches()
    st.stop()

columns = st.segmented_control("Columns", ["Essentials", "All"], default="Essentials", label_visibility="collapsed",
                               help="Essentials: the columns you decide on. All: the scores, the reasons and the rest.")
table = shown.assign(company=[c or f"{s} (company hidden)" for c, s in zip(shown["company"], shown["source"])], applied=False, noise=False)
edited = st.data_editor(
    table, key=table_key("jobs"), hide_index=True, height=620,
    # a tick in Applied or Noise is saved at once (tracker/decide.py); the status and note edits wait for the Save button
    on_change=save_edits, args=("jobs", apply_decisions, [int(j) for j in table["job_id"]]),
    # what you decide on first, then the rest
    column_order=[c for c in ["applied", "noise", "score", "title", "company", "role", "place", "source", "posted_ago", "skill_matches", "years_required", "why", "cv_match",
                              "status", "note", "job_url", "target_company", "cv_coverage", "cv_evidence", "skills_matched", "skills_missing",
                              "company_size", "posted_at", "first_seen"]
                  # Claude's columns only with features.grade, the CV match only with features.embeddings
                  if (FEATURES["grade"] or c not in ("score", "why")) and (FEATURES["embeddings"] or c != "cv_match")
                  and (columns == "All" or c in ESSENTIALS | {"applied", "noise"})],
    disabled=[c for c in table.columns if c not in ("status", "note", "applied", "noise")],
    column_config={
        "applied": st.column_config.CheckboxColumn("Applied", width="small", help="Tick it when you applied: saved at once, the job leaves the lists"),
        "noise": st.column_config.CheckboxColumn("Noise", width="small", help="Tick it when the job is noise (applications closed): saved at once, the job is hidden"),
        "score": st.column_config.ProgressColumn("Score", min_value=0, max_value=10, format="%d", width="small",  # a number: a click sorts 10 above 9
                                                help="Claude's grade of the job against your CVs, 0-10; empty when not graded"),
        "title": st.column_config.TextColumn("Title", width=300),
        "company": st.column_config.TextColumn("Company", width=130),
        "place": st.column_config.TextColumn("Where", width="small"),
        "posted_ago": st.column_config.TextColumn("Posted", width=110, help="Apply within 48 hours of the posting; past 3 days, its real age"),
        "posted_at": st.column_config.DatetimeColumn("Posted at", format="D MMM, h:mma", timezone="Africa/Cairo"),
        "why": st.column_config.TextColumn("Why", width="medium", help="Why the job is hidden, else Claude's reason for its score"),
        "skill_matches": st.column_config.NumberColumn("Skills", width="small", help="How many of your skills the job asks for"),
        "years_required": st.column_config.NumberColumn("Years", width="small", format="%d+",
                                                        help="The years of experience the posting asks for"),
        "cv_coverage": st.column_config.ProgressColumn("CV covers", min_value=0, max_value=100, format="%d%%",
                                                       help="How much of what the job asks for your best CV shows"),
        "cv_match": st.column_config.ProgressColumn("CV match", min_value=0, max_value=100, format="%d%%",
                                                    help="How close the job's text is to your nearest CV (55%) and to "
                                                         "your closest single CV line (45%), by embeddings"),
        "cv_evidence": st.column_config.TextColumn("Closest CV line", width="large",
                                                   help="The line of your CVs the job is closest to: why it matched"),
        "target_company": st.column_config.CheckboxColumn("Target"),
        "company_size": st.column_config.TextColumn("Company size", help="Employees, when a job board gives it (Indeed, freehire)"),
        "status": st.column_config.SelectboxColumn("Status", options=STATUSES, required=True),
        "note": st.column_config.TextColumn("Note", width="medium"),
        "role": st.column_config.TextColumn("Role", width=130), "source": "Source",
        "job_url": st.column_config.LinkColumn("Link", display_text="Open", width="small"),
        "first_seen": "First seen",
        "skills_matched": "Your skills it asks for",
        "skills_missing": st.column_config.TextColumn("Not on your CV", help="Skills the job asks for that your best CV does not show"),
    })
changed = edited[(edited["status"] != shown["status"]) | (edited["note"].fillna("") != shown["note"].fillna(""))]
if st.button(f"Save {plural(len(changed), 'change')}", type="primary", disabled=changed.empty, icon=":material/save:"):
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
    st.toast(f"Saved {plural(len(changed), 'change')}", icon=":material/check_circle:")
    st.rerun()
