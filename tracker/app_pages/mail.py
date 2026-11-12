"""Inbox: every email of your mailboxes (spam too), read whole, with what each needs from you. Done ticks it off."""

import pandas as pd
import streamlit as st

from job_radar.db import connect
from job_radar.mail_inbox import set_done
from tracker.common import query

st.title("Inbox", icon=":material/inbox:")
mail = query("SELECT mailbox, message_id, folder, received_at, sender, subject, body, action, urgency, due, reviewed_at, done, topic"
             " FROM core.mail ORDER BY received_at DESC")
if mail.empty:
    st.info("No email stored yet: `python -m job_radar.mail_inbox collect` reads the last 14 days of every folder.", icon=":material/inbox:")
    st.stop()
open_ = mail["action"].notna() & ~mail["done"] & mail["reviewed_at"].notna()
todo = open_ & (mail["topic"] == "job")  # a job, interview or assessment; tools, code and rides are under Other
VIEWS = {"To do": todo, "Other": open_ & (mail["topic"] != "job"), "Done": mail["action"].notna() & mail["done"],
         "Nothing needed": mail["action"].isna() & mail["reviewed_at"].notna(), "Not read yet": mail["reviewed_at"].isna(), "All": mail["subject"].notna()}
view = st.segmented_control("Show", [f"{name} ({int(mask.sum())})" for name, mask in VIEWS.items()], default=f"To do ({int(todo.sum())})",
                            label_visibility="collapsed")
chosen = next((name for name in VIEWS if view and view.startswith(name + " (")), "To do")
rows = mail[VIEWS[chosen]]
if chosen == "To do":  # still open first (due soonest, then undated, newest first), what is past its date last
    due = pd.to_datetime(rows["due"])
    rows = (rows.assign(overdue=due < pd.Timestamp.now().normalize(), undated=due.isna())
            .sort_values(["overdue", "undated", "due", "received_at"], ascending=[True, True, True, False]))
if rows.empty:
    st.success("Nothing here.", icon=":material/check_circle:")


def done(mailbox: str, message_id: str) -> None:
    with connect() as conn:
        set_done(conn, mailbox, message_id)
    query.clear()


for n, m in enumerate(rows.itertuples()):
    with st.container(border=True):
        st.markdown(f"**{m.subject or '(no subject)'}**")
        st.caption(f"{m.sender} · {m.received_at.tz_convert('Africa/Cairo'):%a %d %b, %H:%M} · {m.folder} · {m.mailbox}"
                   + (f" · due {m.due:%d %b}" + (" (past)" if m.due < pd.Timestamp.now().date() else "")
                      if m.due is not None and str(m.due) != "NaT" else "")
                   + (f" · {m.urgency}" if isinstance(m.urgency, str) and m.urgency else ""))
        if isinstance(m.action, str) and m.action:
            st.markdown(f"**What you need to do:** {m.action}")
        elif m.reviewed_at is None or str(m.reviewed_at) == "NaT":
            st.caption("Not read yet.")
        else:
            st.caption("Read: nothing needed from you.")
        with st.expander("The whole email"):
            st.text(m.body)
        if isinstance(m.action, str) and m.action and not m.done:
            st.button("Done", key=f"done-{n}-{m.message_id}", type="primary", icon=":material/check:", on_click=done,
                      args=(m.mailbox, m.message_id))
