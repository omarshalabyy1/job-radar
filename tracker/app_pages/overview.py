"""Overview: how many jobs you can apply to, how many you applied to, and where each application stands, from your
statuses and your mailbox together (job_radar/overview.py)."""

from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from tracker.applications_tab import render as follow_ups
from tracker.common import load_jobs, query
from job_radar.overview import MAIL, STAGES, STATUSES, applications, counts, in_range

LABELS = {"waiting": "Waiting", "viewed": "Viewed", "assessment": "Assessment", "interview": "Interview",
          "rejected": "Rejected", "offer": "Offer"}
HELP = {"waiting": "Applied, no reply yet", "viewed": "The employer opened or is reviewing it, no decision yet",
        "assessment": "An assessment, a test or a video step, spotted by its words in the email",
        "interview": "Invited to or had an interview, and no rejection since",
        "rejected": "Rejected, after any interview or assessment", "offer": "An offer, or hired"}
RANGES = {"Last 30 days": 30, "Last 90 days": 90, "All": None}

st.title("Overview", icon=":material/dashboard:")
jobs = load_jobs()
open_jobs = jobs[jobs["status"].isin(["new", "saved"]) & jobs["hidden_reason"].isna()]
picked = st.segmented_control("Range", list(RANGES), default="Last 30 days", label_visibility="collapsed",
                              help="Applications with any news in this time: a confirmation, a reply or your status")
apps = in_range(applications(list(query(MAIL).itertuples(index=False)), list(query(STATUSES).itertuples(index=False))),
                RANGES[picked or "Last 30 days"], datetime.now(timezone.utc))
n = counts(apps)

with st.container(horizontal=True):  # side by side on a desktop, wrapping on a phone
    st.metric("Can apply", len(open_jobs), border=True, width=170,
              help=f"Open jobs (new or saved) that no rule hides; {int((open_jobs['fresh_level'] <= 3).sum())} posted in the "
                   "last 48 hours. The Jobs page lists them.")
    st.metric("Applied", n["applied"], border=True, width=170, help="Companies you applied to: each company once")
    st.metric("Responses", n["responses"], border=True, width=170, help="Applied minus Waiting: any reply, a view included")
with st.container(horizontal=True):
    for stage in STAGES:
        st.metric(LABELS[stage], n[stage], border=True, width=140, help=HELP[stage])

show = st.segmented_control("Show", ["All", *LABELS.values()], default="All", label_visibility="collapsed")
table = pd.DataFrame(apps, columns=["company", "job", "board", "stage", "applied_on", "latest_at", "latest_email",
                                    "job_url"])
if show and show != "All":
    table = table[table["stage"] == next(k for k, v in LABELS.items() if v == show)]
if table.empty:
    st.info("No applications here in this range.", icon=":material/inbox:")
else:
    first = {"interview": 0, "assessment": 1}  # what needs you soonest on top, then the newest news
    table = (table.assign(order=table["stage"].map(first).fillna(2)).sort_values(["order", "latest_at"], ascending=[True, False])
             .assign(stage=lambda t: t["stage"].map(LABELS)))
    st.dataframe(table, hide_index=True, column_order=["company", "job", "board", "stage", "applied_on", "latest_at",
                                                       "latest_email", "job_url"], column_config={
        "company": st.column_config.TextColumn("Company", width=160), "job": st.column_config.TextColumn("Job", width=240),
        "board": st.column_config.TextColumn("Board", width="small"), "stage": st.column_config.TextColumn("Stage", width="small"),
        "applied_on": st.column_config.DatetimeColumn("Applied on", format="D MMM YYYY", timezone="Africa/Cairo",
                                                      help="The first confirmation email, else when you set your status"),
        "latest_at": st.column_config.DatetimeColumn("Latest news", format="D MMM YYYY", timezone="Africa/Cairo",
                                                     help="The newest email or status change"),
        "latest_email": st.column_config.TextColumn("Latest email", width="large"),
        "job_url": st.column_config.LinkColumn("Job", display_text="Open", width="small")})

st.divider()
follow_ups(query)  # follow up today, the replies to follow-ups, the drafts
