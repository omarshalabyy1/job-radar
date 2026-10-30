"""The tracker's Ready to apply tab: the drafts the tailor step wrote for your best-graded jobs."""

import streamlit as st

from job_radar.config import BOARD_HOSTS
from job_radar.db import connect
from job_radar.tailor import draft_day, draft_folder

FILES = [("cv.md", "CV"), ("cover-letter.md", "Cover letter"), ("notes.md", "Notes"), ("forms.md", "Form answers")]
# the apply page: the employer's or original board's own (core.job.apply_url), else the careers page of a company
# you track when the job's link is only a board's listing, else the job's link
NAME = r"regexp_replace(lower({}), '[\W_]+', '', 'g')"
BOARDS = "|".join(BOARD_HOSTS).replace(".", r"\.")
# the drafts ready: Apply today first (scored 8+ and posted within 48 hours, mart.job_status.fresh_level 1-3), then
# the rest, best score first
DRAFTS = ("SELECT j.job_id, j.title, j.company, g.score, a.note,"
          f" coalesce(j.apply_url, CASE WHEN j.job_url ~* '{BOARDS}' THEN c.careers_url END, j.job_url) AS apply_link,"
          " coalesce(g.score >= 8 AND s.fresh_level <= 3, false) AS apply_today"
          " FROM core.application a JOIN core.job j USING (job_id) LEFT JOIN core.claude_grade g USING (job_id)"
          " LEFT JOIN mart.job_status s USING (job_id)"
          f" LEFT JOIN core.company c ON {NAME.format('c.company')} = {NAME.format('j.company')}"
          " WHERE a.status = 'saved' AND a.note LIKE 'draft ready%'"
          " ORDER BY apply_today DESC, g.score DESC NULLS LAST, a.updated_at DESC")


def mark_applied(query, job_id: int) -> None:
    """The job is applied: its draft leaves this list."""
    with connect() as conn:
        conn.execute("UPDATE core.application SET status = 'applied', note = concat_ws('; ', note, 'applied by you ' || current_date),"
                     " updated_at = now() WHERE job_id = %s", (job_id,))
    query.clear()


def render(query) -> None:
    shown, group = 0, None
    for d in query(DRAFTS).itertuples():
        folder = draft_folder(day, d.company, d.title, d.job_id) if (day := draft_day(d.note)) else None
        if folder is None or not folder.is_dir():
            continue
        shown += 1
        if d.apply_today != group:  # a heading over each group; the rest gets one only under an Apply today group
            if d.apply_today or group is not None:
                st.subheader("Apply today" if d.apply_today else "More drafts, best score first")
            group = d.apply_today
        score = "n/a" if d.score is None else f"{int(d.score)}/10"
        with st.expander(f"{score} · {d.title} @ {d.company}"):
            st.markdown(f"[Apply]({d.apply_link})")
            st.markdown((folder / "cover-letter.md").read_text(encoding="utf-8"))
            files = [(name, label) for name, label in FILES if (folder / name).exists()]  # a draft from before forms.md has none
            for column, (name, label) in zip(st.columns(len(files)), files):
                column.download_button(label, (folder / name).read_bytes(), file_name=f"{folder.name}-{name}",
                                       mime="text/markdown", key=f"{d.job_id}-{name}")
            st.button("Mark applied", key=f"{d.job_id}-applied", icon=":material/check:", on_click=mark_applied,
                      args=(query, d.job_id))
    if not shown:
        st.caption("No drafts yet: jobs graded 8 or more get a CV, cover letter and notes here.")
