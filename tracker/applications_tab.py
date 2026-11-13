"""The tracker's Follow-ups page, and the funnel and company sections of the Interview rate page, from your mailbox
(core.application_email)."""

import os
from pathlib import Path

import pandas as pd
import streamlit as st

from job_radar.applications import application_stages, due_now, follow_up_answers, funnel_counts
from job_radar.config import OUTPUT
from job_radar.db import connect

GHOSTED_DAYS = 30  # applied this many days ago and no answer since (an interview, a rejection or an offer): ghosted

# one row per company (an email naming no company: per title), so a rejection naming a title sits with
# LinkedIn's "your application was sent to X", which names none; latest news is the newest email, or ghosted, or
# hired once any email says so (a later signature reminder does not undo it); started is your start date there
# (core.hire, set by hand), matched by letters and digits alone; the company is shown as most of its emails spell it
APPLICATIONS = f"""
SELECT coalesce(mode() WITHIN GROUP (ORDER BY company), '') AS company, coalesce(string_agg(DISTINCT title, ', '), '') AS titles,
       count(*) FILTER (WHERE kind = 'applied') AS applications, min(received_at) FILTER (WHERE kind = 'applied') AS first_applied,
       CASE WHEN bool_or(kind = 'hired') THEN 'hired'
            WHEN min(received_at) FILTER (WHERE kind = 'applied') < now() - interval '{GHOSTED_DAYS} days'
                 AND NOT bool_or(kind IN ('interview', 'rejected', 'offer', 'hired')) THEN 'ghosted'
            ELSE (array_agg(kind ORDER BY received_at DESC))[1] END AS latest_news,
       max(received_at) AS latest_date, count(*) AS emails,
       (SELECT h.started_on FROM core.hire h WHERE regexp_replace(lower(h.company), '[\\W_]+', '', 'g')
                                                 = regexp_replace(lower(max(e.company)), '[\\W_]+', '', 'g')) AS started_on
FROM core.application_email e WHERE kind <> 'other'
GROUP BY coalesce(lower(company), lower(title))
ORDER BY latest_date DESC"""

# how many companies (an email naming none: its title) sent each kind of news, matched by letters and digits alone
# ("ACME LABS" is "Acme Labs"), and how many ghosted you; an email naming neither is not counted
COMPANIES_BY_KIND = f"""
WITH mail AS (SELECT coalesce(nullif(regexp_replace(lower(company), '[\\W_]+', '', 'g'), ''), lower(title)) AS who, kind,
                     received_at FROM core.application_email)
SELECT kind, count(DISTINCT who) AS n FROM mail GROUP BY kind
UNION ALL
SELECT 'ghosted', count(*) FROM (SELECT who FROM mail WHERE who IS NOT NULL GROUP BY who
    HAVING min(received_at) FILTER (WHERE kind = 'applied') < now() - interval '{GHOSTED_DAYS} days'
       AND NOT bool_or(kind IN ('interview', 'rejected', 'offer', 'hired'))) g"""

# every company with a job in core.job now (a job you ignored or were rejected for is not open), with
# your history there; the history matches a company by its letters and digits alone
INTELLIGENCE = """
WITH history AS (
    SELECT regexp_replace(lower(company), '[\\W_]+', '', 'g') AS name, count(*) FILTER (WHERE kind = 'applied') AS applications,
           (array_agg(kind ORDER BY received_at DESC))[1] AS last_outcome
    FROM core.application_email WHERE kind <> 'other' AND company IS NOT NULL GROUP BY 1)
SELECT j.company, count(*) FILTER (WHERE j.status IN ('new', 'saved', 'applied', 'interview', 'offer')) AS open_jobs,
       string_agg(DISTINCT j.role, ', ') AS roles, bool_or(j.target_company) AS starred,
       coalesce(h.applications, 0) AS applications, h.last_outcome
FROM mart.job_status j
LEFT JOIN history h ON h.name = regexp_replace(lower(j.company), '[\\W_]+', '', 'g')
GROUP BY j.company, h.applications, h.last_outcome
ORDER BY coalesce(h.applications, 0) DESC, bool_or(j.target_company) DESC, open_jobs DESC, j.company"""


# the emails that tell an application's stage (core.application_email), for the funnel
STAGE_EMAILS = """
SELECT company, title, kind, received_at, sender FROM core.application_email
WHERE kind IN ('applied', 'viewed', 'interview', 'offer', 'hired')"""
STAGE_LABELS = {"applied": "Applied", "viewed": "Viewed", "interview": "Interview", "offer": "Offer", "hired": "Hired"}


def funnel_table(applications: list[dict], by: str | None) -> pd.DataFrame:
    """One row per month or channel (by), or one row 'All': applications that reached each stage, and the share of
    the applied ones that did."""
    groups = {"All": applications} if by is None else {}
    for a in applications if by else []:
        groups.setdefault(a[by], []).append(a)
    rows = []
    for name, group in sorted(groups.items(), reverse=by == "month"):  # the newest month first
        counts = funnel_counts(group)
        rows.append({by or "": name, **{STAGE_LABELS[k]: n for k, n in counts.items()},
                     **{f"{STAGE_LABELS[k]} %": round(100 * n / counts["applied"], 1) for k, n in counts.items() if k != "applied"}})
    return pd.DataFrame(rows)


def follow_ups(conn) -> pd.DataFrame:
    """Today's due follow-ups: applications.due_now, the rule the email and the drafts use (7 or 14 Cairo days after the
    first confirmation email, no answer yet)."""
    return pd.DataFrame(due_now(conn), columns=["company", "title", "applied", "days"])


def drafts() -> list[tuple[str, str, Path]]:
    """(kind, name, file) of the follow-up and interview-prep drafts on disk, newest folder first."""
    out = [("Follow-up", f"{f.parent.name}  {f.stem}", f) for f in sorted((OUTPUT / "followups").glob("*/*.md"), reverse=True)]
    return out + [("Interview prep", f.parent.name, f) for f in sorted((OUTPUT / "interviews").glob("*/notes.md"))]


def render(query) -> None:
    st.caption("Follow-ups for your applications, read from your mailbox (Gmail, read-only) by the track_applications step. "
               "The Overview counts and lists every application.")
    mails = query(COMPANIES_BY_KIND).set_index("kind")["n"]
    if (mails.drop("other", errors="ignore") == 0).all():  # the ghosted row is always there
        st.info("No application emails yet: the next track_applications run reads them from your mailbox.")
        return

    st.subheader("Follow up", icon=":material/schedule_send:")
    st.caption("Due today: applied exactly 7 or 14 Cairo days ago and no answer yet, the same list as the email and the drafts. "
               "The Overview shows each company's latest news.")
    with connect() as conn:
        due = follow_ups(conn)
        replies = pd.DataFrame(follow_up_answers(conn), columns=["company", "title", "followed_up", "answer", "answered_on"])
    if due.empty:
        st.write("Nothing to follow up today.")
    else:
        st.dataframe(due, hide_index=True, column_config={
            "company": "Company", "title": "Title", "applied": st.column_config.DateColumn("Applied", format="D MMM YYYY"),
            "days": "Days ago"})

    st.subheader("Follow-up replies", icon=":material/mark_email_read:")
    st.caption("Each application a follow-up was drafted for (output/followups), and the first news after the draft's Cairo "
               "day: a view, a rejection, an interview or an offer. A draft counts whether or not you sent it.")
    if replies.empty:
        st.write("No follow-up drafts yet.")
    else:
        answered = int(replies["answer"].notna().sum())
        st.metric("Answered after a follow-up", f"{answered} of {len(replies)} ({round(100 * answered / len(replies), 1)}%)",
                  border=True, width=260)
        replies["answer"] = replies["answer"].str.capitalize().fillna("None yet")
        st.dataframe(replies, hide_index=True, column_config={
            "company": "Company", "title": "Title", "followed_up": st.column_config.DateColumn("Followed up", format="D MMM YYYY"),
            "answer": "Answer", "answered_on": st.column_config.DateColumn("Answered on", format="D MMM YYYY")})

    st.subheader("Drafts", icon=":material/edit_note:")
    st.caption("Follow-up emails (7 or 14 days after applying, no answer yet) and interview notes, written by Claude from your CV. "
               "Nothing is sent.")
    files = drafts()
    if not files:
        st.write("No drafts yet: turn on features.follow_up_drafts or features.interview_prep in settings.yaml.")
    for kind, name, file in files[:30]:
        label, button = st.columns([4, 1])
        label.write(f"{kind}: {name}")
        button.download_button("Download", file.read_bytes(), file_name=f"{file.parent.name}-{file.name}", mime="text/markdown",
                               key=str(file))


def render_funnel(query) -> None:
    """The funnel by month and channel (on the Interview rate page)."""
    st.subheader("Funnel", icon=":material/filter_alt:")
    st.caption("Applications (one per company) that reached each stage, by the first confirmation email's month and where it "
               "came from. A stage counts when its own email or a later stage's came, so the numbers only fall; the % is of "
               "the applied ones. Rejections are not a stage.")
    stages = query(STAGE_EMAILS)
    applications = application_stages(list(stages.itertuples(index=False)), os.environ.get("GMAIL_USER", ""))
    if applications:
        for label, by in (("Overall", None), ("By month", "month"), ("By channel", "channel")):
            st.markdown(f"**{label}**")
            st.dataframe(funnel_table(applications, by), hide_index=True,
                         column_config={"month": "Month", "channel": "Channel"})


def render_companies(query) -> None:
    """Every company with a job in the tracker now, and what you sent it (on the Interview rate page)."""
    st.subheader("Company intelligence", icon=":material/apartment:")
    st.caption("Every company with a job in the tracker now, with what you have sent it: applications are the "
               "confirmation emails, last outcome is the newest email from it.")
    companies = query(INTELLIGENCE)
    companies["last_outcome"] = companies["last_outcome"].str.capitalize()
    st.dataframe(companies, hide_index=True, column_order=["company", "starred", "open_jobs", "applications", "last_outcome", "roles"],
                 column_config={
        "company": "Company", "open_jobs": "Open jobs", "roles": "Roles", "starred": st.column_config.CheckboxColumn("Starred"),
        "applications": "Applications", "last_outcome": "Last outcome"})
