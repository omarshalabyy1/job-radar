"""Last run: how each step of the radar last went, and what Claude cost."""

import streamlit as st

from tracker.common import query
from tracker.runlog_tab import render

st.title("Last run", icon=":material/history:")
render(query)
