"""The tracker's Overview: one row per company you applied to, with its stage, from your statuses (core.application) and
your mailbox (core.application_email) together, and the counts of each stage. Pure: the page does the queries."""

import re
from collections import Counter
from datetime import datetime, timedelta

from job_radar.applications import channel, name_key

# the current stage of an application, each company in exactly one, so the counts add up to Applied
STAGES = ["waiting", "viewed", "assessment", "interview", "rejected", "offer"]
# the mail reader files an assessment or a test as an interview: these words in its subject or Inbox action split it out
ASSESSMENT = re.compile(r"assessment|\btest\b|exam|hackerrank|codility|video application|\btask\b|challenge", re.I)
# a job board named in a job's source ("apify:linkedin_remote", "manual:wuzzuf-keywords") or an apply attempt
BOARDS = {"linkedin": "LinkedIn", "indeed": "Indeed", "wuzzuf": "Wuzzuf", "ashby": "Ashby", "workable": "Workable",
          "naukrigulf": "Naukrigulf", "bayt": "Bayt", "glassdoor": "Glassdoor"}
# what a company with one of these counts as an application: a confirmation, your own status, or a job that came to you
APPLIED = {"applied", "interview", "assessment", "offer", "hired", "rejected-status"}

# the application news, an email matched to a job going by that job's company; the Inbox's action helps spot assessments
MAIL = ("SELECT coalesce(j.company, e.company), coalesce(e.title, j.title), e.kind, e.received_at, e.subject, m.action, e.sender"
        " FROM core.application_email e LEFT JOIN core.job j USING (job_id)"
        " LEFT JOIN core.mail m ON m.message_id = e.message_id AND m.mailbox = e.mailbox WHERE e.kind <> 'other'")
# your statuses, with the board of the job's latest apply attempt
STATUSES = ("SELECT j.company, j.title, a.status, a.updated_at, j.source,"
            " (SELECT t.board FROM core.apply_attempt t WHERE t.job_id = a.job_id ORDER BY t.started_at DESC LIMIT 1),"
            " j.job_url FROM core.application a JOIN core.job j USING (job_id)"
            " WHERE a.status IN ('applied', 'interview', 'offer', 'rejected')")


def email_kind(kind: str, subject: str | None, action: str | None) -> str:
    """An interview email that is an assessment or a test, by its words, is 'assessment'; any other kind stays."""
    return "assessment" if kind == "interview" and ASSESSMENT.search(f"{subject or ''} {action or ''}") else kind


def board(attempt_board: str | None, source: str | None, sender: str | None) -> str:
    """Where the application went: the apply attempt's board, else the board in the job's source, else where the first
    confirmation email came from; blank when nothing says."""
    for text in (attempt_board, source):
        for word, name in BOARDS.items():
            if word in (text or "").lower():
                return name
    if source and source != "email" and not source.startswith("manual"):
        return source
    return channel(sender) if sender else ""


def stage(events: list[tuple[str, datetime]]) -> str:
    """Offer (or hired) first; else a rejection newer than any interview or assessment; else the furthest news. A rejection
    or a view from before your latest application there (you applied again) no longer counts; an interview still does."""
    again = max((at for k, at in events if k == "applied"), default=None)
    events = [(k, at) for k, at in events if again is None or at >= again or k not in ("rejected", "viewed")]
    kinds = {k for k, _ in events}
    if kinds & {"offer", "hired"}:
        return "offer"
    rejected = max((at for k, at in events if k == "rejected"), default=None)
    talked = max((at for k, at in events if k in ("interview", "assessment")), default=None)
    if rejected and (talked is None or rejected >= talked):
        return "rejected"
    return next((s for s in ("interview", "assessment", "viewed") if s in kinds), "waiting")


def applications(mail: list[tuple], statuses: list[tuple]) -> list[dict]:
    """One dict per company (an email naming none: its title) that you applied to. `mail`: (company, title, kind,
    received_at, subject, action, sender); `statuses`: (company, title, status, updated_at, source, attempt_board, job_url)."""
    groups: dict[str, dict] = {}

    def group(company, title):
        key = name_key(company) or (title or "").lower()
        return groups.setdefault(key, {"names": Counter(), "titles": [], "events": [], "mail": [], "status": []}) if key else None

    for company, title, kind, at, subject, action, sender in mail:
        if (g := group(company, title)) is not None:
            g["names"][company or ""] += 1
            g["titles"].append(title)
            g["events"].append((email_kind(kind, subject, action), at))
            g["mail"].append((at, subject, sender, kind))
    for company, title, value, at, source, attempt_board, url in statuses:
        if (g := group(company, title)) is not None:
            g["names"][company or ""] += 100  # the job's spelling of the company wins
            g["titles"].insert(0, title)
            g["events"].append(("rejected-status" if value == "rejected" else value, at))
            g["status"].append((at, source, attempt_board, url))
    out = []
    for g in groups.values():
        kinds = {k for k, _ in g["events"]}
        if not kinds & APPLIED:
            continue
        events = [("rejected" if k == "rejected-status" else k, at) for k, at in g["events"]]
        mails, status = sorted(g["mail"], key=lambda m: m[0]), sorted(g["status"], key=lambda s: s[0])
        first_sender = next((m[2] for m in mails if m[3] == "applied"), mails[0][2] if mails else None)
        last_status = status[-1] if status else (None, None, None, None)
        out.append({
            "company": g["names"].most_common(1)[0][0],
            "job": ", ".join(dict.fromkeys(t for t in g["titles"] if t)),
            "board": board(last_status[2], last_status[1], first_sender),
            "stage": stage(events),
            # the first confirmation email, else when you set your status (no other date says when you applied)
            "applied_on": next((m[0] for m in mails if m[3] == "applied"), status[0][0] if status else None),
            "latest_at": max(at for _, at in events),
            "latest_email": mails[-1][1] if mails else None,
            "job_url": last_status[3]})
    return out


def in_range(apps: list[dict], days: int | None, now: datetime) -> list[dict]:
    """The applications with news in the last `days` days (all of them for None)."""
    return [a for a in apps if days is None or a["latest_at"] >= now - timedelta(days=days)]


def counts(apps: list[dict]) -> dict[str, int]:
    """How many applications, how many got any response, and how many are at each stage."""
    n = Counter(a["stage"] for a in apps)
    return {"applied": len(apps), "responses": len(apps) - n["waiting"], **{s: n[s] for s in STAGES}}
