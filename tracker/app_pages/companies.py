"""Companies: your companies from settings.yaml, and how each careers page was last read."""

import streamlit as st

from tracker.common import query

st.title("Companies", icon=":material/apartment:")
st.caption("Your companies, from settings.yaml (companies): add, change or remove them there and the next run updates this "
           "list. A starred company's jobs are starred in the email and the tracker. With a careers page, the next run "
           "detects its platform (Workable, Greenhouse, Lever, Ashby, Phenom, SuccessFactors, RSS, or any other page, read "
           "with Playwright) and reads its jobs every run. Sites that turn scripts away are skipped until you open them "
           "once with scripts/open_blocked.py.")
companies = query("SELECT company, starred, careers_url, platform, note, checked_at FROM core.company ORDER BY starred DESC, company")
st.dataframe(companies, hide_index=True, column_config={
    "company": "Company", "starred": st.column_config.CheckboxColumn("Starred"),
    "careers_url": st.column_config.LinkColumn("Careers page"), "platform": "Read as",
    "note": "Why not read", "checked_at": st.column_config.DatetimeColumn("Checked", format="D MMM, HH:mm")})
