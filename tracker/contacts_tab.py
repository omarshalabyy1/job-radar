"""The tracker's Contacts tab: recruiters and hiring managers at the companies you applied to, from
data/contacts.csv. People's names and links stay out of the public repo: data/ is gitignored."""

from pathlib import Path

import pandas as pd
import streamlit as st

CONTACTS = Path(__file__).resolve().parent.parent / "data" / "contacts.csv"
BLUE, ORANGE = "#3987e5", "#d95926"  # app.py's two accents


def render() -> None:
    if not CONTACTS.exists():
        st.caption("No data/contacts.csv yet (gitignored): columns company, side (Recruiting, Hiring, Founder or Email), name, "
                   "title, location, linkedin, email, why.")
        return
    st.caption("Recruiters and hiring managers at the companies you applied to, from data/contacts.csv "
               "(gitignored): add, change or remove rows there. Nothing here sends a message.")
    contacts = pd.read_csv(CONTACTS)
    sides = list(dict.fromkeys(contacts["side"]))
    side = st.segmented_control("Show", sides, key="contacts_side",
                                format_func=lambda s: f"{s} ({(contacts['side'] == s).sum()})")
    if side:
        contacts = contacts[contacts["side"] == side]
    contacts = contacts.fillna("").sort_values(["company", "name"], key=lambda c: c.str.lower())  # an empty cell, not "None"; by company
    contacts["side"] = contacts["side"].map(lambda s: [s])  # one badge per row
    st.dataframe(contacts, hide_index=True,
                 column_order=["name", "company", "side", "title", "why", "linkedin", "email", "location"],
                 column_config={
                     "side": st.column_config.MultiselectColumn(
                         "Side", options=["Recruiting", "Hiring", "Founder", "Email"], color=[BLUE, ORANGE, "green", "gray"],
                         help="Recruiting: talent acquisition and HR. Hiring: the team the role sits in. "
                              "Founder: a startup's founder or CEO, for the outreach drafts."),
                     "name": st.column_config.TextColumn("Name", pinned=True),
                     "title": st.column_config.TextColumn("Title", width="medium"),
                     "company": st.column_config.TextColumn("Company", width="small"),
                     "location": st.column_config.TextColumn("Location", width="small"),
                     "linkedin": st.column_config.LinkColumn("LinkedIn", display_text="Open profile"),
                     "email": st.column_config.LinkColumn("Email", display_text=r"mailto:(.*)", width="medium"),
                     "why": st.column_config.TextColumn("Why reach them", width="large")})
