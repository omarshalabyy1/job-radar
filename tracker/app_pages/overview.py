"""Overview: how many jobs you can apply to, how many you applied to, and where each application stands, from your
statuses and your mailbox together (job_radar/overview.py)."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

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
CAIRO = ZoneInfo("Africa/Cairo")

st.title("Overview", icon=":material/dashboard:")
jobs = load_jobs()
open_jobs = jobs[jobs["status"].isin(["new", "saved"]) & jobs["hidden_reason"].isna()]
everything = applications(list(query(MAIL).itertuples(index=False)), list(query(STATUSES).itertuples(index=False)))
now = datetime.now(timezone.utc)

# what needs you: every assessment or interview with news in the last 30 days, and your open deadlines (Today page)
st.subheader("Needs you", icon=":material/priority_high:")
steps = [{"what": LABELS[a["stage"]], "company": a["company"], "job": a["job"], "due": a["due"], "news": a["latest_email"],
          "on": a["latest_at"], "link": a["job_url"]} for a in in_range(everything, 30, now) if a["stage"] in ("assessment", "interview")]
deadlines = query("SELECT d.due, d.what, d.note, coalesce(j.company, '') AS company, coalesce(j.title, '') AS title, j.job_url"
                  " FROM core.deadline d LEFT JOIN core.job j USING (job_id)"
                  " WHERE NOT d.done AND d.due >= (now() AT TIME ZONE 'Africa/Cairo')::date - 14")
steps += [{"what": f"Deadline: {d.what}", "company": d.company, "job": d.title, "due": d.due, "news": d.note, "on": None,
           "link": d.job_url} for d in deadlines.itertuples()]
if not steps:
    st.caption("Nothing waits on you: no assessment, interview or open deadline.")
else:
    today = now.astimezone(CAIRO).date()
    todo = pd.DataFrame(steps).assign(
        when=lambda t: [("" if pd.isna(d) else "Overdue" if d < today else "Today" if d == today else f"In {(d - today).days} days")
                        for d in t["due"]]).sort_values(["due", "on"], ascending=[True, False], na_position="last")
    st.dataframe(todo, hide_index=True, column_order=["what", "company", "job", "due", "when", "news", "link"], column_config={
        "what": st.column_config.TextColumn("To do", width=170), "company": st.column_config.TextColumn("Company", width=150),
        "job": st.column_config.TextColumn("Job", width=220),
        "due": st.column_config.DateColumn("Due", format="ddd D MMM", help="Only when the email or your deadline gives a date"),
        "when": st.column_config.TextColumn("When", width="small"),
        "news": st.column_config.TextColumn("Latest news", width="large"),
        "link": st.column_config.LinkColumn("Job", display_text="Open", width="small")})

picked = st.segmented_control("Range", list(RANGES), default="Last 30 days", label_visibility="collapsed",
                              help="Applications with any news in this time: a confirmation, a reply or your status")
apps = in_range(everything, RANGES[picked or "Last 30 days"], now)
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

# what does not match between your statuses and your mailbox, in this range (check_applications links what it can, hourly)
no_receipt = [a for a in apps if a["in_tracker"] and not a["receipt"]]
mail_only = [a for a in apps if not a["in_tracker"]]
with st.expander(f"Checks: {len(no_receipt)} with no confirmation email, {len(mail_only)} confirmed by email but not in the tracker",
                 icon=":material/rule:"):
    columns = {"company": "Company", "job": "Job", "board": "Board", "applied_on": st.column_config.DatetimeColumn(
        "Applied on", format="D MMM YYYY", timezone="Africa/Cairo")}
    st.markdown("**In the tracker, no confirmation email yet**: applied by you or Claude; a board that sends no email, "
                "or one that has not come.")
    st.dataframe(pd.DataFrame(no_receipt, columns=list(columns)), hide_index=True, column_config=columns)
    st.markdown("**Confirmed by email, not in the tracker**: applied outside the radar, or the email names no company or job "
                "the radar knows.")
    st.dataframe(pd.DataFrame(mail_only, columns=list(columns)), hide_index=True, column_config=columns)

st.divider()
follow_ups(query)  # follow up today, the replies to follow-ups, the drafts
