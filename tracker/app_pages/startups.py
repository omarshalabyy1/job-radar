"""Startups: the small companies found for your search (output/startups/startups.csv), and whether the radar reads them."""

import pandas as pd
import streamlit as st

from job_radar.config import OUTPUT, SETTINGS, is_target

st.title("Startups", icon=":material/rocket_launch:")
path = OUTPUT / "startups" / "startups.csv"
if not path.exists():
    st.info(f"No startup list yet: it is {path}, written by Claude in /job-radar when you ask for a startup search.",
            icon=":material/info:")
    st.stop()
startups = pd.read_csv(path, dtype=str).fillna("")
ORDER = ["Egypt", "Egypt (office)", "Gulf", "Gulf (office)", "US", "US (office)", "Europe", "Europe (office)"]
startups = startups.sort_values(["region", "name"], key=lambda c: c.map(ORDER.index) if c.name == "region" else c.str.lower(),
                                kind="stable").reset_index(drop=True)
read = {c["name"].lower() for c in SETTINGS["companies"]}
startups["radar"] = ["Careers page read" if n.lower() in read else "Starred" if is_target(n) else ""
                     for n in startups["name"]]
st.caption(f"{len(startups)} startups of about 50 people or fewer, found {pd.Timestamp(path.stat().st_mtime, unit='s'):%d %b %Y}: "
           "LinkedIn's company search (through Apify), Y Combinator, Wellfound, F6S, StartupList Africa, Wamda and the "
           "companies' own job boards. Egypt first, then the Gulf, the US and Europe; '(office)' means an office there and "
           "its headquarters elsewhere. In the radar: a careers page read every collect (settings.yaml companies), or "
           "starred, so its jobs on any board come first (your_companies).")

left, middle, right = st.columns([2, 2, 3])
regions = left.multiselect("Region", list(dict.fromkeys(startups["region"])), placeholder="All regions")
kinds = middle.multiselect("Kind", sorted(startups["kind"].unique()), placeholder="AI/data and products")
search = right.text_input("Search name or what it does", placeholder="e.g. Arabic, fintech, RAG")
careers_only = st.toggle("Only those with a careers page")
keep = pd.Series(True, index=startups.index)
if regions:
    keep &= startups["region"].isin(regions)
if kinds:
    keep &= startups["kind"].isin(kinds)
if careers_only:
    keep &= startups["careers"] != ""
shown = startups[keep]
if search:
    shown = shown[shown["name"].str.contains(search, case=False, regex=False) | shown["what"].str.contains(search, case=False, regex=False)]

with st.container(horizontal=True):
    st.metric("Showing", len(shown), border=True, width=160)
    st.metric("With a careers page", int((shown["careers"] != "").sum()), border=True, width=200)
    st.metric("Read every collect", int((shown["radar"] == "Careers page read").sum()), border=True, width=200)
    st.metric("Starred", int((shown["radar"] == "Starred").sum()), border=True, width=160)
st.dataframe(shown, hide_index=True, height=640,
             column_order=["region", "name", "city", "size", "kind", "what", "careers", "website", "radar", "source"],
             column_config={
                 "region": st.column_config.TextColumn("Region", width="small"), "name": st.column_config.TextColumn("Startup", width=170),
                 "city": "City", "size": st.column_config.TextColumn("People", width="small"), "kind": st.column_config.TextColumn("Kind", width="small"),
                 "what": st.column_config.TextColumn("What it does", width="large"),
                 "careers": st.column_config.LinkColumn("Careers", display_text="Open", width="small"),
                 "website": st.column_config.LinkColumn("Website", display_text="Site", width="small"),
                 "radar": st.column_config.TextColumn("In the radar", width=140), "source": "Found through"})
