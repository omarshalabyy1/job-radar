"""The applications view: one row per job you applied to, with its stage, from your statuses (core.application) and
your mailbox (core.application_email) together, and the counts of each stage, for the tracker's Overview and the check_applications step."""

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

# the application news, an email matched to a job going by that job's company and title; the Inbox's action helps spot assessments
MAIL = ("SELECT coalesce(j.company, e.company), coalesce(j.title, e.title), e.kind, e.received_at, e.subject, m.action, e.sender"
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


def words(title: str | None) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (title or "").lower()))


def applications(mail: list[tuple], statuses: list[tuple]) -> list[dict]:
    """One dict per job you applied to: its company and title (letters and digits alone). An email goes, oldest first, to
    the application with its company and title; else, when it names no company, to the one company with that title; else
    to an application at its company whose title says the same in fewer words (a reply: in more words too); else a reply
    or an email naming no title goes to your latest application there sent on or before it, else your first there.
    `mail`: (company, title, kind, received_at, subject, action, sender); `statuses`: (company, title, status, updated_at,
    source, attempt_board, job_url)."""
    groups: dict[tuple[str, str], dict] = {}

    def group(key, title):
        return groups.setdefault(key, {"names": Counter(), "titles": Counter(), "events": [], "mail": [], "status": [],
                                       "words": words(title)})

    for company, title, value, at, source, attempt_board, url in statuses:
        g = group((name_key(company), name_key(title)), title)
        g["names"][company or ""] += 100  # the job's spelling wins
        g["titles"][title or ""] += 100
        g["events"].append(("rejected-status" if value == "rejected" else value, at))
        g["status"].append((at, source, attempt_board, url))

    def latest_before(found, at):
        found = sorted(found, key=lambda g: min(t for _, t in g["events"]))
        before = [g for g in found if min(t for _, t in g["events"]) <= at]
        return before[-1] if before else found[0]

    for row in sorted(mail, key=lambda r: r[3]):
        company, title, kind, at = row[:4]
        ck, tk, w = name_key(company), name_key(title), words(title)
        if not ck and not tk:
            continue
        if tk and (ck, tk) in groups:
            g = groups[(ck, tk)]
        elif not ck:
            named = {key[0] for key in groups if key[1] == tk}
            g = groups[(named.pop(), tk)] if len(named) == 1 else group(("", tk), title)
        else:
            there = [g for key, g in groups.items() if key[0] == ck and g["events"]]
            similar = [g for g in there if w and g["words"] and (w <= g["words"] or (kind != "applied" and g["words"] <= w))]
            if similar:
                g = latest_before(similar, at)
            elif there and (kind != "applied" or not tk):
                g = latest_before(there, at)
            else:
                g = group((ck, tk), title)
        g["names"][company or ""] += 1
        g["titles"][title or ""] += 1
        g["events"].append((email_kind(kind, row[4], row[5]), at))
        g["mail"].append((at, row[4], row[6], kind))
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
            "company": next((n for n, _ in g["names"].most_common() if n), ""),
            "job": next((t for t, _ in g["titles"].most_common() if t), ""),
            "board": board(last_status[2], last_status[1], first_sender),
            "stage": stage(events),
            # the first confirmation email, else when you set your status (no other date says when you applied)
            "applied_on": next((m[0] for m in mails if m[3] == "applied"), status[0][0] if status else None),
            "latest_at": max(at for _, at in events),
            "latest_email": mails[-1][1] if mails else None,
            "job_url": last_status[3],
            "receipt": any(m[3] == "applied" for m in mails),  # a confirmation email came
            "in_tracker": bool(status)})  # you or Claude set a status for it
    return out


def in_range(apps: list[dict], days: int | None, now: datetime) -> list[dict]:
    """The applications with news in the last `days` days (all of them for None)."""
    return [a for a in apps if days is None or a["latest_at"] >= now - timedelta(days=days)]


def counts(apps: list[dict]) -> dict[str, int]:
    """How many applications, how many got any response, and how many are at each stage."""
    n = Counter(a["stage"] for a in apps)
    return {"applied": len(apps), "responses": len(apps) - n["waiting"], **{s: n[s] for s in STAGES}}
