"""CV: the CVs every job is matched against."""

import streamlit as st
from pypdf import PdfReader

from job_radar.db import connect
from tracker.common import plural, query

st.title("CV", icon=":material/description:")
st.caption("Upload your CV as a PDF. The next run shows, for every job, how much of what it asks for your CV covers, and "
           "hands your newest CV to the career-ops export. It stays in your warehouse.")
with st.form("add_cv", clear_on_submit=True):
    label = st.text_input("Label", value="Data Engineer & BI Developer")
    pdf = st.file_uploader("CV (PDF)", type=["pdf"])
    if st.form_submit_button("Upload", type="primary", icon=":material/upload:"):
        if not pdf or not label.strip():
            st.error("Choose a PDF and give it a label, then upload.")
        else:
            with st.spinner("Reading your CV"):
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
    "uploaded_at": st.column_config.DatetimeColumn("Uploaded", format="D MMM, HH:mm", timezone="Africa/Cairo"),
    "skills": st.column_config.TextColumn("Skills found on it", help="Matched by the next run (match_skills)")})
drop = st.multiselect("CVs to remove", cvs["label"])
with st.popover("Remove CVs", disabled=not drop, icon=":material/delete:"):
    st.write(f"Remove {plural(len(drop), 'CV')} for good? This cannot be undone.")
    if st.button("Remove for good", type="primary"):
        with connect() as conn:
            conn.execute("DELETE FROM core.cv WHERE label = ANY(%s)", (drop,))
        query.clear()
        st.rerun()
