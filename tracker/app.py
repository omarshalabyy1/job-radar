"""Job tracker: every job the radar found, your status for each, and which of your skills are in demand.

Run by docker-compose.yml on http://127.0.0.1:8501, or from the repo root with .env set:
    streamlit run tracker/app.py
"""

import sys
from pathlib import Path

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


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def query(sql: str) -> pd.DataFrame:
    with connect() as conn:
        cur = conn.execute(sql)
        return pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])


st.set_page_config(page_title="Job tracker", page_icon=":material/work:", layout="wide")
st.title("Job tracker")

jobs = query("SELECT job_id, skill_matches, cv_coverage, target_company, title, company, role, place, source, status,"
             " note, first_seen, job_url, skills_matched FROM mart.job_status"
             " ORDER BY role_rank, target_company DESC, skill_matches DESC, first_seen DESC")

role_options, place_options = sorted(jobs["role"].unique()), sorted(jobs["place"].unique())
# The filters live in the address, so a reload or a bookmark keeps them.
if "role" not in st.session_state:
    url = st.query_params
    skills_in_url = url.get("skills", "0")
    st.session_state.update(
        role=[r for r in url.get_all("role") if r in role_options],
        where=[p for p in url.get_all("where") if p in place_options],
        status=[s for s in url.get_all("status") if s in STATUSES] or OPEN,
        skills=min(int(skills_in_url), 10) if skills_in_url.isdigit() else 0,
        q=url.get("q", ""))

with st.sidebar:
    roles = st.multiselect("Role", role_options, key="role")
    places = st.multiselect("Where", place_options, key="where")
    statuses = st.multiselect("Status", STATUSES, key="status")
    min_skills = st.slider("At least this many of your skills", 0, 10, key="skills")
    text = st.text_input("Search title or company", key="q")
st.query_params.from_dict({k: v for k, v in
                           {"role": roles, "where": places, "status": statuses, "skills": min_skills, "q": text}.items()
                           if v})

shown = jobs[(jobs["role"].isin(roles) if roles else True)
             & (jobs["place"].isin(places) if places else True)
             & (jobs["status"].isin(statuses) if statuses else True)
             & (jobs["skill_matches"] >= min_skills)
             & ((jobs["title"] + " " + jobs["company"]).str.contains(text, case=False, regex=False) if text else True)]

counts = jobs["status"].value_counts()
for column, status in zip(st.columns(5), ["new", "saved", "applied", "interview", "offer"]):
    column.metric(status.title(), int(counts.get(status, 0)))

jobs_tab, skills_tab, companies_tab, cv_tab = st.tabs(["Jobs", "Skills in demand", "Companies", "CV"])

with jobs_tab:
    if shown.empty:
        st.info("No jobs match these filters: clear one in the sidebar to see more.")
    else:
        edited = st.data_editor(
            shown, key="jobs", hide_index=True,
            disabled=[c for c in shown.columns if c not in ("status", "note")],
            column_config={
                "job_id": None,
                "skill_matches": st.column_config.NumberColumn("Skills", help="How many of your skills the job asks for"),
                "cv_coverage": st.column_config.ProgressColumn("CV covers", min_value=0, max_value=100, format="%d%%",
                                                               help="How much of what the job asks for your best CV shows"),
                "target_company": st.column_config.CheckboxColumn("Target"),
                "status": st.column_config.SelectboxColumn("Status", options=STATUSES, required=True),
                "job_url": st.column_config.LinkColumn("Link", display_text="View job"),
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

with skills_tab:
    skills = query("SELECT role_rank, role, track, skill, jobs, pct_of_role FROM mart.skill_demand"
                   " ORDER BY role_rank, jobs DESC")
    if skills.empty:
        st.info("No job descriptions yet: the skills appear after the first run's describe step.")
    else:
        role = st.selectbox("Role", skills["role"].unique())
        track = st.segmented_control("Course", ["Data Engineering", "Generative AI"], default="Data Engineering")
        top = skills[(skills["role"] == role) & ((skills["track"] == track) if track else True)].head(20)
        st.caption("Share of the last 90 days' jobs of this role that ask for each of your skills.")
        st.bar_chart(top, x="skill", y="pct_of_role", horizontal=True, sort="-pct_of_role",
                     x_label="% of jobs", y_label="")

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
                if not text.strip():
                    st.error("No text found in this PDF (a scanned image?): export it from Word or Google Docs as PDF.")
                else:
                    with connect() as conn:
                        conn.execute("INSERT INTO core.cv (label, filename, text) VALUES (%s, %s, %s) ON CONFLICT (label)"
                                     " DO UPDATE SET filename = EXCLUDED.filename, text = EXCLUDED.text, uploaded_at = now()",
                                     (label.strip(), pdf.name, text))
                    st.rerun()
    cvs = query("SELECT label, filename, length(text) AS characters, uploaded_at FROM core.cv ORDER BY uploaded_at DESC")
    st.dataframe(cvs, hide_index=True, column_config={
        "uploaded_at": st.column_config.DatetimeColumn("Uploaded", format="D MMM, HH:mm")})
    drop = st.multiselect("CVs to remove", cvs["label"])
    with st.popover("Remove CVs", disabled=not drop):
        st.write(f"Remove {plural(len(drop), 'CV')} for good? This cannot be undone.")
        if st.button("Remove for good", type="primary"):
            with connect() as conn:
                conn.execute("DELETE FROM core.cv WHERE label = ANY(%s)", (drop,))
            st.rerun()
