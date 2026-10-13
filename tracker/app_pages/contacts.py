"""Contacts: recruiters and hiring managers at the companies you applied to."""

import streamlit as st

from tracker.contacts_tab import render

st.title("Contacts", icon=":material/contacts:")
render()
