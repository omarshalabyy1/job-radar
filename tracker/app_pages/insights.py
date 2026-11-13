"""Insights: which of your applications reached an interview, by source, role, score and the posting's age; the funnel
and the companies you applied to."""

import streamlit as st

from tracker.applications_tab import render_companies, render_funnel
from tracker.common import query
from tracker.insights_tab import render

st.title("Interview rate", icon=":material/query_stats:")
render()
render_funnel(query)
render_companies(query)
