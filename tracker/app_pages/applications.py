"""Applications: your applications, as your mailbox tells them."""

import streamlit as st

from tracker.applications_tab import render
from tracker.common import query

st.title("Applications", icon=":material/mail:")
render(query)
