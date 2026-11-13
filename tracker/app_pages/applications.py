"""Follow-ups: what to follow up today, the replies to follow-ups, and the drafts. The Overview counts and lists the applications."""

import streamlit as st

from tracker.applications_tab import render
from tracker.common import query

st.title("Follow-ups", icon=":material/schedule_send:")
render(query)
