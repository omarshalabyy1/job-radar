"""The applications view: one row per job you applied to, with its stage, from your statuses (core.application) and
your mailbox (core.application_email) together, and the counts of each stage, for the tracker's Overview and the check_applications step."""

import re
import unicodedata
from collections import Counter
from urllib.parse import quote
from zoneinfo import ZoneInfo
from datetime import date, datetime, timedelta

from job_radar.applications import channel, name_key

# the current stage of an application, each company in exactly one, so the counts add up to Applied
CAIRO = ZoneInfo("Africa/Cairo")
# "viewed" is not a stage: an employer opening the application is no reply, it still waits (Omar, 2026-11-14)
STAGES = ["waiting", "assessment", "interview", "rejected", "offer"]
# a confirmation email and the status you set for the same application are this close: the email often comes first
NEAR_DAYS = 3
# an interview or assessment with no news this many days after its day was ghosted, which counts as a rejection (Omar, 2026-11-14)
GHOST_DAYS = 14
# the mail reader files an assessment or a test as an interview: these words in its subject or Inbox action split it out
ASSESSMENT = re.compile(r"assessment|\btest\b|exam|hackerrank|codility|video application|\btask\b|challenge", re.I)
# in the body (the Inbox stores it) only the plain words: a body says "test" or "task" about anything
BODY_ASSESSMENT = re.compile(r"assessment|hackerrank|codility|online test|coding test|coding challenge|video application", re.I)
# a deadline the email itself states: "expires in 1 days", "(5 days left)", "within 48 hours"
DUE = re.compile(r"(?:expires? in|within)\s+(\d+)\s+(day|hour)s?|(\d+)\s+(day|hour)s?\s+left", re.I)
# the hard limit when the email gives one, before any softer "ideally within 3 days": "After 7 days, the assessment
# invitation link will expire" (IBM), "the link will expire after 24 hours"
EXPIRY = re.compile(r"after\s+(\d+)\s+(day|hour)s?[^.]{0,80}\bexpire|\bexpire\w*\s+after\s+(\d+)\s+(day|hour)s?", re.I)
# a calendar invitation's own day: "Invitation: ... @ Thu Oct 29, 2026 2pm"
CALENDAR = re.compile(r"@ \w{3} (\w{3}) (\d{1,2}), (\d{4})")
# a job board named in a job's source ("apify:linkedin_remote", "manual:wuzzuf-keywords") or an apply attempt
BOARDS = {"linkedin": "LinkedIn", "indeed": "Indeed", "wuzzuf": "Wuzzuf", "ashby": "Ashby", "workable": "Workable",
          "naukrigulf": "Naukrigulf", "bayt": "Bayt", "glassdoor": "Glassdoor"}
# what a company with one of these counts as an application: a confirmation, your own status, or a job that came to you
APPLIED = {"applied", "interview", "assessment", "offer", "hired", "rejected-status"}

# the application news, an email matched to a job going by that job's company and title; the Inbox's action helps spot assessments
MAIL = ("SELECT coalesce(j.company, e.company), coalesce(j.title, e.title), e.kind, e.received_at, e.subject, m.action, e.sender,"
        " e.message_id, e.job_id, m.body, e.mailbox"
        " FROM core.application_email e LEFT JOIN core.job j USING (job_id)"
        " LEFT JOIN core.mail m ON m.message_id = e.message_id AND m.mailbox = e.mailbox WHERE e.kind <> 'other'")
# your statuses, with the board of the job's latest apply attempt and your open deadline for the job (Today page)
STATUSES = ("SELECT j.company, j.title, a.status, a.updated_at, j.source,"
            " (SELECT t.board FROM core.apply_attempt t WHERE t.job_id = a.job_id ORDER BY t.started_at DESC LIMIT 1),"
            " j.job_url, a.job_id, a.note,"
            " (SELECT min(d.due) FROM core.deadline d WHERE d.job_id = a.job_id AND NOT d.done)"
            " FROM core.application a JOIN core.job j USING (job_id)"
            " WHERE a.status IN ('applied', 'interview', 'offer', 'rejected')")


def email_kind(kind: str, subject: str | None, action: str | None, body: str | None = None) -> str:
    """An interview email that is an assessment or a test, by the words of its subject, its Inbox action or its body, is
    'assessment'; any other kind stays."""
    if kind != "interview":
        return kind
    return "assessment" if ASSESSMENT.search(f"{subject or ''} {action or ''}") or BODY_ASSESSMENT.search(body or "") else kind


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


def due_date(text: str, received_at: datetime) -> date | None:
    """The day a step is due, when the email says it ("expires in 1 days", "5 days left", "within 48 hours", "after 7 days
    the link will expire", a calendar invitation's day); else None."""
    if c := CALENDAR.search(text or ""):
        return datetime.strptime(" ".join(c.groups()), "%b %d %Y").date()
    if not (m := EXPIRY.search(text or "") or DUE.search(text or "")):
        return None
    n, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    return (received_at + timedelta(**{f"{unit.lower()}s": int(n)})).date()


def gmail_link(mailbox: str | None, message_id: str) -> str:
    """A link that opens one email in Gmail, in the account it came to (Gmail's rfc822msgid search)."""
    return (f"https://mail.google.com/mail/u/?authuser={quote(mailbox or '', safe='')}"
            f"#search/rfc822msgid%3A{quote(message_id.strip('<>'), safe='')}")


def stage(events: list[tuple[str, datetime]]) -> str:
    """Offer (or hired) first; else a rejection newer than any interview or assessment; else the furthest news. A rejection
    or a view from before your latest application there (you applied again) no longer counts; an interview still does."""
    again = max((at for k, at in events if k == "applied"), default=None)
    events = [(k, at) for k, at in events if again is None or at >= again or k != "rejected"]
    kinds = {k for k, _ in events}
    if kinds & {"offer", "hired"}:
        return "offer"
    rejected = max((at for k, at in events if k == "rejected"), default=None)
    talked = max((at for k, at in events if k in ("interview", "assessment")), default=None)
    if rejected and (talked is None or rejected >= talked):
        return "rejected"
    if talked:  # the newest step: an assessment after an interview is where it stands now, and the other way round
        return max((at, k) for k, at in events if k in ("interview", "assessment"))[1]
    return "waiting"


def words(title: str | None) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (title or "").lower()))


def company_keys(names) -> dict[str, str]:
    """Each company's key: its words' letters and digits, accents dropped ("Müller`s" is "Muller's"), and a name that only
    adds words to another's is that company ("Advansys ME" is Advansys, "Systems Limited - Egypt" is Systems Limited);
    "Older" is not "Old", "TPG Telecom" is not "TP"."""
    def tokens(name):
        return tuple(re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()))
    known = sorted({tokens(n) for n in names} - {()}, key=len)
    short = {t: next((b for b in known if t[:len(b)] == b and len("".join(b)) >= 3), t) for t in known}
    return {n: "".join(short.get(tokens(n), ())) for n in names}


def applications(mail: list[tuple], statuses: list[tuple], now: datetime | None = None) -> list[dict]:
    """One dict per job you applied to: its company (company_keys) and title (letters and digits alone). Your statuses come
    first, then the emails, oldest first. An email naming a job goes to the
    application with its company and title; when it names no company (Indeed's own confirmation), to the application with
    that title that has no confirmation yet and began within NEAR_DAYS of it, else to one that began within a day, else to
    the one company with that title;
    else to an application at its company whose title says the same in fewer words (a reply: in more words too). A
    confirmation naming no job goes to the application there with no confirmation yet that began within NEAR_DAYS of it
    (the email often comes before the status is set), else to one that began within a day (two emails of one application),
    else, like a reply, to your latest application there sent on or before it; with none, it starts its own.
    `mail`: (company, title, kind, received_at, subject, action, sender, message_id, job_id, body, mailbox); `statuses`: (company,
    title, status, updated_at, source, attempt_board, job_url, job_id, note, deadline)."""
    groups: dict[tuple[str, str], dict] = {}
    company = company_keys({r[0] for r in mail} | {r[0] for r in statuses})

    def group(key, title):
        return groups.setdefault(key, {"names": Counter(), "titles": Counter(), "events": [], "mail": [], "status": [],
                                       "words": words(title)})

    for name, title, value, at, source, attempt_board, url, job_id, note, deadline in statuses:
        g = group((company[name], name_key(title)), title)
        g["names"][name or ""] += 100  # the job's spelling wins
        g["titles"][title or ""] += 100
        g["events"].append(("rejected-status" if value == "rejected" else value, at))
        g["status"].append((at, source, attempt_board, url, job_id, note, value, deadline))

    def began(g):
        return min(t for _, t in g["events"])

    def latest_before(found, at):
        found = sorted(found, key=began)
        before = [g for g in found if began(g) <= at]
        return before[-1] if before else found[0]

    def nearest(found, at, days, unconfirmed=False):
        """The application of `found` that began closest to `at`, within `days`; with `unconfirmed`, only one in the
        tracker that has no confirmation email yet."""
        found = [g for g in found if abs(began(g) - at) <= timedelta(days=days)
                 and (not unconfirmed or (g["status"] and not any(m[3] == "applied" for m in g["mail"])))]
        return min(found, key=lambda g: abs(began(g) - at)) if found else None

    for row in sorted(mail, key=lambda r: r[3]):
        name, title, kind, at = row[:4]
        ck, tk, w = company[name], name_key(title), words(title)
        if not ck and not tk:
            continue
        by_title = True  # matched by its title, so check_applications may link it to the job
        if tk and (ck, tk) in groups:
            g = groups[(ck, tk)]
        elif not ck:
            same = {key: g for key, g in groups.items() if key[1] == tk and key[0]}
            g = (nearest(same.values(), at, NEAR_DAYS, unconfirmed=True) or nearest(same.values(), at, 1)) if kind == "applied" else None
            if g is None:
                g = next(iter(same.values())) if len(same) == 1 else group(("", tk), title)
        else:
            there = [g for key, g in groups.items() if key[0] == ck and g["events"]]
            # a reply, or an email naming no job, is about an application you had sent by then
            pool = there if kind == "applied" and tk else [g for g in there if began(g) <= at]
            similar = [g for g in pool if w and g["words"] and (w <= g["words"] or (kind != "applied" and g["words"] <= w))]
            near = (nearest(there, at, NEAR_DAYS, unconfirmed=True) or nearest(there, at, 1)) if kind == "applied" and not tk else None
            if similar:
                g = latest_before(similar, at)
            elif near:
                g, by_title = near, False
            elif pool and (kind != "applied" or not tk):
                g, by_title = latest_before(pool, at), False
            elif kind == "applied" and (ck, "") in groups and abs(began(groups[(ck, "")]) - at) <= timedelta(days=1):
                g = groups[(ck, tk)] = groups.pop((ck, ""))  # the confirmation naming no job came first: it is this application
                g["words"] = w
            else:
                g = group((ck, tk), title)
        g["names"][name or ""] += 1
        g["titles"][title or ""] += 1
        g["events"].append((email_kind(kind, row[4], row[5], row[9]), at))
        g["mail"].append((at, row[4], row[6], kind, row[7], row[8], by_title, row[9], row[10]))
    out = []
    for g in groups.values():
        kinds = {k for k, _ in g["events"]}
        if not kinds & APPLIED:
            continue
        events = [("rejected" if k == "rejected-status" else k, at) for k, at in g["events"]]
        mails, status = sorted(g["mail"], key=lambda m: m[0]), sorted(g["status"], key=lambda s: s[0])
        first_sender = next((m[2] for m in mails if m[3] == "applied"), mails[0][2] if mails else None)
        last_status = status[-1] if status else (None, None, None, None, None, None, None, None)
        current = stage(events)
        # the day the newest interview or assessment email states, else your own deadline for the job
        due = next((due_date(f"{m[1]} {m[7] or ''}", m[0]) for m in reversed(mails) if m[3] == "interview"), None) or last_status[7]
        latest = max(at for _, at in events)
        # with a clock: an interview or assessment and no news GHOST_DAYS after its day (the invitation's, else the newest news)
        step_day = max(filter(None, [due, latest.astimezone(CAIRO).date()]))
        ghosted = (now is not None and current in ("interview", "assessment")
                   and (now.astimezone(CAIRO).date() - step_day).days >= GHOST_DAYS)
        out.append({
            "company": next((n for n, _ in g["names"].most_common() if n), ""),
            "job": next((t for t, _ in g["titles"].most_common() if t), ""),
            "board": board(last_status[2], last_status[1], first_sender),
            "stage": "rejected" if ghosted else current,
            # the first confirmation email, else when you set your status (no other date says when you applied)
            "applied_on": next((m[0] for m in mails if m[3] == "applied"), status[0][0] if status else None),
            "latest_at": latest,
            # the newest news in words: when you moved it to interview or offer after the last email, or told what happened,
            # the last part of your
            # note ("HR screening interview, told by Omar ..."); else that email's subject
            "latest_email": f"No news since the {current} on {step_day:%d %b}: ghosted" if ghosted
                            else note.rsplit("; ", 1)[-1] if (note := last_status[5])
                            and (last_status[6] in ("interview", "offer") or "told by Omar" in note.rsplit("; ", 1)[-1])
                            and (not mails or last_status[0] >= mails[-1][0]) else mails[-1][1] if mails else None,
            "job_url": last_status[3],
            "email_link": gmail_link(mails[-1][8], mails[-1][4]) if mails else None,  # the newest email, in its mailbox
            "link": (last_status[3] if (last_status[3] or "").startswith("http") else None)  # the job page (a web page), else the email
                    or (gmail_link(mails[-1][8], mails[-1][4]) if mails else None),
            "receipt": any(m[3] == "applied" for m in mails),  # a confirmation email came
            # the due day the newest interview or assessment email states, if it states one
            "due": due,
            "job_id": last_status[4],  # the job you set a status for (the latest), None for an email alone
            # the emails matched to it by title and linked to no job yet: (message_id, kind, received_at); a reply from
            # before the application started (its first confirmation or your status) is about an older one
            "to_link": [(m[4], m[3], m[0]) for m in mails if m[6] and m[5] is None
                        and (m[3] == "applied" or m[0] >= min([x[0] for x in mails if x[3] == "applied"] + [s[0] for s in status]))]
                       if last_status[4] else [],
            "in_tracker": bool(status)})  # you or Claude set a status for it
    return out


def needs_you(apps: list[dict], now: datetime) -> list[dict]:
    """The assessments and interviews with news in the last 30 days that are not past their due day (Cairo), the soonest
    due first, then the ones with no date, newest news first."""
    today = now.astimezone(CAIRO).date()
    open_ = [a for a in in_range(apps, 30, now) if a["stage"] in ("assessment", "interview") and (a["due"] is None or a["due"] >= today)]
    return sorted(open_, key=lambda a: (a["due"] is None, a["due"] or today, -a["latest_at"].timestamp()))


def in_range(apps: list[dict], days: int | None, now: datetime) -> list[dict]:
    """The applications with news in the last `days` days (all of them for None)."""
    return [a for a in apps if days is None or a["latest_at"] >= now - timedelta(days=days)]


def counts(apps: list[dict]) -> dict[str, int]:
    """How many applications, how many got any response, and how many are at each stage."""
    n = Counter(a["stage"] for a in apps)
    return {"applied": len(apps), "responses": len(apps) - n["waiting"], **{s: n[s] for s in STAGES}}
