"""The tracker's Insights page: how many of your applications reached an interview, by source, role, Claude's score and
how old the posting was when you applied. It changes no ranking: too little data to rank by yet (2026-10-30)."""

import pandas as pd
import streamlit as st

from job_radar.applications import OUTCOMES, job_outcomes
from job_radar.db import connect
from tracker.applications_tab import GHOSTED_DAYS

SMALL = 10  # a group with fewer applications than this is not reliable
INTERVIEWED = ["interview", "offer", "hired"]  # reached an interview, whatever came after
SCORES = ["0-5", "6-7", "8-10", "Not graded"]
AGES = ["48 hours or less", "2-7 days", "Over 7 days", "Unknown"]
# the application news, an email matched to a job going by that job's company (as the follow-ups read it)
MAIL = ("SELECT coalesce(j.company, e.company), e.title, e.kind, e.received_at, e.job_id"
        " FROM core.application_email e LEFT JOIN core.job j USING (job_id) WHERE e.kind <> 'other'")
STATUSES = ("SELECT a.job_id, j.company, a.status FROM core.application a JOIN core.job j USING (job_id)"
            " WHERE a.status IN ('applied', 'interview', 'offer', 'rejected')")
JOBS = ("SELECT j.job_id, j.source, j.role, j.posted_at, g.score FROM core.job j LEFT JOIN core.claude_grade g USING (job_id)"
        " WHERE j.job_id IN (SELECT job_id FROM core.application UNION SELECT job_id FROM core.application_email)")


def score_band(score) -> str:
    return "Not graded" if score is None else "0-5" if score <= 5 else "6-7" if score <= 7 else "8-10"


def age_band(applied_at, posted_at) -> str:
    """How old the posting was when you applied; Unknown with no confirmation email or no posting time. Applying before
    the radar's posting time (a source that gives none) counts as 48 hours or less."""
    if applied_at is None or posted_at is None:
        return "Unknown"
    hours = (applied_at - posted_at).total_seconds() / 3600
    return "48 hours or less" if hours <= 48 else "2-7 days" if hours <= 7 * 24 else "Over 7 days"


def applications(mail: list[tuple], statuses: list[tuple], jobs: list[tuple], now) -> pd.DataFrame:
    """One row per job you applied to that is still in core.job: its source, role, score band, age band and outcome."""
    info = {job_id: (source, role, posted_at, score) for job_id, source, role, posted_at, score in jobs}
    rows = []
    for job_id, (outcome, applied_at) in job_outcomes(mail, statuses, now, GHOSTED_DAYS).items():
        if job_id in info:
            source, role, posted_at, score = info[job_id]
            rows.append({"source": source, "role": role, "score": score_band(score), "age": age_band(applied_at, posted_at),
                         "outcome": outcome})
    return pd.DataFrame(rows, columns=["source", "role", "score", "age", "outcome"])


def rates(apps: pd.DataFrame, by: str, order: list[str] | None = None) -> pd.DataFrame:
    """One row per group of `by`: applications, interviews and the interview rate, then how many ended at each outcome;
    the biggest group first, or in `order`."""
    t = pd.crosstab(apps[by], apps["outcome"]).reindex(columns=OUTCOMES, fill_value=0)
    t.columns = [c.capitalize() for c in t.columns]
    n, interviews = t.sum(axis=1), t[[c.capitalize() for c in INTERVIEWED]].sum(axis=1)
    t.insert(0, "Applications", n)
    t.insert(1, "Interviews", interviews)
    t.insert(2, "Interview rate %", (100 * interviews / n).round(1))
    t = t.reindex([o for o in order if o in t.index]) if order else t.sort_values("Applications", ascending=False, kind="stable")
    return t.rename_axis(by.capitalize()).reset_index()


def render() -> None:
    st.caption("Each application matched to a job, with the furthest stage it reached: applied, viewed, interview, offer, "
               f"hired, rejected or ghosted (applied {GHOSTED_DAYS}+ days ago, no answer). An application is a company, as on "
               "the Applications page. Interview rate: reached an interview, whatever came after. Times are Cairo's.")
    st.caption(f"Groups of under {SMALL} applications are not reliable: one more interview moves their rate a lot.")
    with connect() as conn:
        mail, statuses, jobs = [conn.execute(sql).fetchall() for sql in (MAIL, STATUSES, JOBS)]
    apps = applications(mail, statuses, jobs, pd.Timestamp.now(tz="Africa/Cairo"))
    if apps.empty:
        st.info("No applications matched to a job yet: mark a job applied, or wait for its confirmation email.")
        return
    interviews = int(apps["outcome"].isin(INTERVIEWED).sum())
    st.metric("Interview rate", f"{round(100 * interviews / len(apps), 1)}%", border=True, width=220,
              help=f"{interviews} of {len(apps)} applications reached an interview")
    for label, by, order in (("By source", "source", None), ("By role", "role", None), ("By Claude's score", "score", SCORES),
                             ("By the posting's age when you applied", "age", AGES)):
        st.subheader(label)
        st.dataframe(rates(apps, by, order), hide_index=True)
