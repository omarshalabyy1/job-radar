"""Ready to apply: the drafts Claude prepared for your best-graded jobs."""

import streamlit as st

from tracker.common import query
from tracker.ready_tab import render

st.title("Ready to apply", icon=":material/task_alt:")
render(query)
