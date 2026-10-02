"""Job tracker: every job the radar found, your status for each, and the skills in demand.

Run by docker-compose.yml on http://127.0.0.1:8501, or from the repo root with .env set:
    streamlit run tracker/app.py
"""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")
from job_radar.db import connect  # noqa: E402

STATUSES = ["new", "saved", "applied", "interview", "offer", "rejected", "ignored"]


def query(sql: str) -> pd.DataFrame:
    with connect() as conn:
        cur = conn.execute(sql)
        return pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])


st.set_page_config(page_title="Job tracker", page_icon=":material/work:", layout="wide")
st.title("Job tracker")

jobs = query("SELECT job_id, fit_score, target_company, title, company, role, place, source, status, note,"
             " first_seen, job_url, fit_reason FROM mart.job_status"
             " ORDER BY role_rank, target_company DESC, fit_score DESC NULLS LAST, first_seen DESC")

with st.sidebar:
    roles = st.multiselect("Role", sorted(jobs["role"].unique()))
    places = st.multiselect("Where", sorted(jobs["place"].unique()))
    statuses = st.multiselect("Status", STATUSES, default=["new", "saved", "applied", "interview", "offer"])
    min_fit = st.slider("Minimum fit score", 0, 100, 0)
    text = st.text_input("Search title or company")

shown = jobs[(jobs["role"].isin(roles) if roles else True)
             & (jobs["place"].isin(places) if places else True)
             & (jobs["status"].isin(statuses) if statuses else True)
             & ((jobs["fit_score"].fillna(0) >= min_fit) if min_fit else True)
             & ((jobs["title"] + " " + jobs["company"]).str.contains(text, case=False, regex=False) if text else True)]

counts = jobs["status"].value_counts()
for column, status in zip(st.columns(5), ["new", "saved", "applied", "interview", "offer"]):
    column.metric(status.title(), int(counts.get(status, 0)))

jobs_tab, skills_tab = st.tabs(["Jobs", "Skills in demand"])

with jobs_tab:
    edited = st.data_editor(
        shown, key="jobs", hide_index=True,
        disabled=[c for c in shown.columns if c not in ("status", "note")],
        column_config={
            "job_id": None,
            "fit_score": st.column_config.ProgressColumn("Fit", min_value=0, max_value=100, format="%d"),
            "target_company": st.column_config.CheckboxColumn("Target"),
            "status": st.column_config.SelectboxColumn("Status", options=STATUSES, required=True),
            "job_url": st.column_config.LinkColumn("Link", display_text="Open"),
            "fit_reason": "Why",
        })
    changed = edited[(edited["status"] != shown["status"]) | (edited["note"].fillna("") != shown["note"].fillna(""))]
    if st.button(f"Save {len(changed)} changes", type="primary", disabled=changed.empty):
        with connect() as conn:
            for job in changed.itertuples():
                if job.status == "new":
                    conn.execute("DELETE FROM core.application WHERE job_id = %s", (job.job_id,))
                else:
                    conn.execute(
                        "INSERT INTO core.application (job_id, status, note) VALUES (%s, %s, %s)"
                        " ON CONFLICT (job_id) DO UPDATE SET status = EXCLUDED.status, note = EXCLUDED.note,"
                        " updated_at = now()", (job.job_id, job.status, job.note))
        st.rerun()

with skills_tab:
    skills = query("SELECT role_rank, role, skill, jobs, pct_of_role FROM mart.skill_demand ORDER BY role_rank, jobs DESC")
    if skills.empty:
        st.info("No job descriptions yet: the skills appear after the first run's describe step.")
    else:
        role = st.selectbox("Role", skills["role"].unique())
        top = skills[skills["role"] == role].head(20)
        st.caption("Share of the last 90 days' jobs of this role that ask for each skill.")
        st.bar_chart(top, x="skill", y="pct_of_role", horizontal=True, sort="-pct_of_role",
                     x_label="% of jobs", y_label="")
