"""Insights: which of your applications reached an interview, by source, role, score and the posting's age."""

import streamlit as st

from tracker.insights_tab import render

st.title("Interview rate", icon=":material/query_stats:")
render()
