"""The tracker's Ready to apply tab: the drafts the tailor step wrote for your best-graded jobs."""

import streamlit as st

from job_radar.tailor import draft_day, draft_folder

FILES = [("cv.md", "CV"), ("cover-letter.md", "Cover letter"), ("notes.md", "Notes")]


def render(query) -> None:
    drafts = query("SELECT j.job_id, j.title, j.company, j.job_url, g.score, a.note FROM core.application a"
                   " JOIN core.job j USING (job_id) LEFT JOIN core.claude_grade g USING (job_id)"
                   " WHERE a.status = 'saved' AND a.note LIKE 'draft ready%'"
                   " ORDER BY g.score DESC NULLS LAST, a.updated_at DESC")
    shown = 0
    for d in drafts.itertuples():
        folder = draft_folder(day, d.company, d.title, d.job_id) if (day := draft_day(d.note)) else None
        if folder is None or not folder.is_dir():
            continue
        shown += 1
        score = "n/a" if d.score is None else f"{int(d.score)}/10"
        with st.expander(f"{score} · {d.title} @ {d.company}"):
            st.markdown(f"[View job]({d.job_url})")
            st.markdown((folder / "cover-letter.md").read_text(encoding="utf-8"))
            for column, (name, label) in zip(st.columns(len(FILES)), FILES):
                column.download_button(label, (folder / name).read_bytes(), file_name=f"{folder.name}-{name}",
                                       mime="text/markdown", key=f"{d.job_id}-{name}")
    if not shown:
        st.caption("No drafts yet: jobs graded 8 or more get a CV, cover letter and notes here.")
