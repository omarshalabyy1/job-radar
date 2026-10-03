"""The two job emails: plain HTML with inline styles, sent through Gmail, or written to output/
without Gmail settings. steps.email picks the jobs and marks them emailed."""

from __future__ import annotations

import html
import os
import re
import smtplib
from datetime import date, datetime
from email.message import EmailMessage

from .config import EXPERIENCE, HOME, PLACES, ROLE_LABEL, ROOT, SCHEDULE, words


def subject(jobs: list[dict], name: str) -> str:
    starred = sum(j["target_company"] for j in jobs)
    return (f"Job radar · {name} · {len(jobs)} new job{'s' * (len(jobs) != 1)}"
            f"{f' · ⭐ {starred} at your companies' if starred else ''} · {date.today():%d %b}")


# The email is plain HTML with inline styles only, the one form every mail client (Gmail first)
# shows as designed: a header with the numbers, then a section per place, in it one per role in
# your order, in that one per employment type, one card per job. The whole email stops at
# EMAIL_BYTES (UTF-8), so it stays under Gmail's ~100 KB clip; the rest are a link away in the tracker.
EMAIL_BYTES = 70_000
PLACE_ORDER = list(dict.fromkeys([HOME, "Remote", *(place for place, _, _ in PLACES)]))
EMPLOYMENT = ["Full-time", "Part-time", "Contract", "Freelance"]
# Experience (settings.yaml), so the further you scroll the more a job asks for: the title's words
# first, else the years the description asks for, else mid level
ENTRY = words(EXPERIENCE["entry"])
SENIOR = words(EXPERIENCE["senior"])
YEARS = (r"\b(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*\d{1,2}\s*)?\+?\s*(?:years?|yrs?)\b(?=[^.]{0,40}experience)"
         r"|experience[^.]{0,40}?\b(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*\d{1,2}\s*)?(?:years?|yrs?)\b"
         r"|خبرة[^.]{0,30}?(\d{1,2})")
LEVELS = ["Entry & junior", "Mid level", "Senior"]
INDEED_TYPES = {"fulltime": "Full-time", "parttime": "Part-time", "contract": "Contract", "temporary": "Contract"}
# the title's words, then only plain statements in the description ("smart contracts" is no contract job)
TYPE_IN_TITLE = {"Freelance": r"freelanc|عمل حر", "Part-time": r"part[- ]?time|دوام جزئي",
                 "Contract": r"\bcontract(or)?\b|fixed[- ]term|\btemp(orary)?\b"}
TYPE_IN_TEXT = {"Freelance": r"\bfreelanc", "Part-time": r"\bpart[- ]time\b",
                "Contract": r"\bcontract (role|position|basis|assignment)|\d+[- ]months? contract|fixed[- ]term"}
# Colours from the dataviz skill's reference palette: text in ink tokens, a deep blue shell, a
# tinted tag per job type.
FONT = "font-family:system-ui,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
INK, INK2, LINE, PLANE = "#0b0b0b", "#52514e", "#e1e0d9", "#f4f4f1"
SHELL, LINK, TINT = "#0d366b", "#1c5cab", "#eef4fc"
TYPE_TAG = {"Part-time": ("#ece9fb", "#3b2e8a"), "Contract": ("#fdeee6", "#9a3b12"), "Freelance": ("#e3f6ee", "#0e6b49")}


def experience(j: dict) -> tuple[int, int | None]:
    """(level, years): level 0 entry or junior, 1 mid level, 2 senior; years, the least the
    description asks for, None when it does not say."""
    m = re.search(YEARS, j.get("description") or "", re.I)
    years = int(next(g for g in m.groups() if g)) if m else None
    years = years if years is not None and years <= 15 else None  # "founded 50 years ago" is no requirement
    if re.search(ENTRY, j["title"], re.I):
        return 0, years
    if re.search(SENIOR, j["title"], re.I):
        return 2, years
    if years is None:
        return 1, years
    return (0 if years <= EXPERIENCE["entry_max_years"] else 2 if years >= EXPERIENCE["senior_min_years"] else 1), years


def employment(j: dict) -> str:
    """Full-time, Part-time, Contract or Freelance: Indeed's job type (full-time first when it gives
    two), else the title, else the description; Full-time when none says."""
    indeed = {INDEED_TYPES.get(kind) for kind in (j.get("job_type") or "").split(", ")}
    for kind in EMPLOYMENT:
        if kind in indeed:
            return kind
    for patterns, text in ((TYPE_IN_TITLE, j["title"]), (TYPE_IN_TEXT, j.get("description") or "")):
        for kind, pattern in patterns.items():
            if re.search(pattern, text, re.I):
                return kind
    return "Full-time"


def tag(text: str, background: str, color: str) -> str:
    return (f'<span style="display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;font-weight:600;'
            f'background:{background};color:{color}">{text}</span>')


def job_card(j: dict) -> str:
    """The title (the link), company · location and a quiet meta line. A job at your companies gets
    a warm ground and a "⭐ Your company" tag."""
    star = j["target_company"]
    kind = j.get("employment", "Full-time")
    source = "your job alerts" if j["source"] == "email" else html.escape(j["source"][:1].upper() + j["source"][1:])
    meta = " · ".join(x for x in (
        tag("⭐ Your company", "#fdf0c4", "#7a5200") if star else "",
        tag(kind, *TYPE_TAG[kind]) if kind in TYPE_TAG else "",
        f"{j['years']}+ yrs" if j.get("years") is not None else "",
        f"via {source}",
        f"{j['date_posted']:%d %b}" if j["date_posted"] else "",
        f"CV {j['cv_coverage']:.0f}%" if j.get("cv_coverage") is not None else "") if x)
    where = " · ".join(html.escape(x) for x in (str(j["company"] or "").strip(" -"), str(j["location"] or "").strip(" -")) if x)
    return (f'<tr><td style="padding:14px 20px;border-top:1px solid {LINE};{"background:#fffaeb;" if star else ""}">'
            f'<a href="{html.escape(j["job_url"])}" style="font-size:16px;line-height:22px;font-weight:600;color:{LINK};'
            f'text-decoration:none">{html.escape(j["title"])}</a>'
            f'<div style="margin-top:2px;font-size:14px;line-height:20px;color:{INK}">{where}</div>'
            f'<div style="margin-top:4px;font-size:12px;line-height:20px;color:{INK2}">{meta}</div></td></tr>')


def digest(jobs: list[dict], name: str) -> str:
    """One column at most 640 px wide, so it reads the same on a phone and on a laptop: a deep blue
    header with one summary line and a chip per place, then a band per place (home first), in it
    a section per experience level (entry and junior, mid, senior: the further you scroll, the more
    a job asks for), a heading per role, a job-type line only when a role mixes types, and the cards."""
    top = jobs[0]  # the best match: the jobs come best first
    for j in jobs:
        j["employment"] = employment(j)
        j["level"], j["years"] = experience(j)
    # fewest years first inside each level (a job that does not say sits with its level's usual
    # years); stable, so the best match stays first among equals
    usual = (0, EXPERIENCE["entry_max_years"] + 1, EXPERIENCE["senior_min_years"])
    jobs = sorted(jobs, key=lambda j: (j["level"], usual[j["level"]] if j["years"] is None else j["years"]))
    places = sorted({j["place"] for j in jobs},
                    key=lambda p: PLACE_ORDER.index(p) if p in PLACE_ORDER else len(PLACE_ORDER))
    by_place = {place: [j for j in jobs if j["place"] == place] for place in places}
    starred = sum(j["target_company"] for j in jobs)
    summary = f"{len(jobs)} new job{'s' * (len(jobs) != 1)}{f' · ⭐ {starred} at your companies' if starred else ''}"
    # the inbox preview line under the subject
    preheader = f"{summary} · top: {top['title']} at {top['company']}"
    chips = "".join(f'<span style="display:inline-block;margin:0 6px 6px 0;padding:4px 10px;border-radius:12px;'
                    f'background:#184f95;font-size:12px;color:#cde2fb">{html.escape(place)} '
                    f'<b style="color:#ffffff">{len(group)}</b></span>' for place, group in by_place.items()
                    ) if len(by_place) > 1 else ""  # one place (the home email): the title already names it
    more = (f'<a href="http://127.0.0.1:8501" style="color:{LINK};text-decoration:none">in your tracker →</a>')
    parts: list[tuple[str, bool]] = []  # (HTML, is a job card), in reading order
    for place, in_place in by_place.items():
        if len(by_place) > 1:
            parts.append((
                f'<tr><td style="padding:16px 20px 14px;background:{TINT};border-top:1px solid {LINE}">'
                f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
                f'<td style="font-size:20px;line-height:26px;font-weight:700;color:{INK}">{html.escape(place)}</td>'
                f'<td align="right" style="font-size:13px;color:{INK2};white-space:nowrap">{len(in_place)} '
                f'job{"s" * (len(in_place) != 1)}</td></tr></table></td></tr>', False))
        for lvl, level_name in enumerate(LEVELS):
            in_level = [j for j in in_place if j["level"] == lvl]
            if not in_level:
                continue
            parts.append((
                f'<tr><td style="padding:22px 20px 6px;border-bottom:2px solid {SHELL}"><table role="presentation" '
                f'width="100%" cellpadding="0" cellspacing="0"><tr><td style="font-size:16px;line-height:22px;'
                f'font-weight:700;color:{SHELL}">{html.escape(level_name)}</td><td align="right" style="font-size:13px;'
                f'color:{INK2}">{len(in_level)}</td></tr></table></td></tr>', False))
            for rank, label in ROLE_LABEL.items():
                in_role = [j for j in in_level if j["role_rank"] == rank]
                if not in_role:
                    continue
                parts.append((
                    f'<tr><td style="padding:16px 20px 8px"><table role="presentation" width="100%" cellpadding="0" '
                    f'cellspacing="0"><tr><td style="font-size:12px;font-weight:700;letter-spacing:.6px;color:{LINK}">'
                    f'{html.escape(label.upper())}</td><td align="right" style="font-size:12px;color:{INK2}">{len(in_role)}'
                    f'</td></tr></table></td></tr>', False))
                mixed = len({j["employment"] for j in in_role}) > 1
                for kind in EMPLOYMENT:
                    group = [j for j in in_role if j["employment"] == kind]
                    if not group:
                        continue
                    if mixed:
                        parts.append((f'<tr><td style="padding:8px 20px 8px">'
                                      f'{tag(f"{kind} · {len(group)}", *TYPE_TAG.get(kind, (PLANE, INK2)))}</td></tr>',
                                      False))
                    parts += [(job_card(j), True) for j in group]
    head = (f'<div style="margin:0;padding:16px 8px;background:{PLANE};{FONT}">'
            f'<div style="display:none;max-height:0;overflow:hidden;opacity:0">{html.escape(preheader)}</div>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;'
            f'margin:0 auto;background:#ffffff;border-radius:14px;overflow:hidden;border:1px solid {LINE};{FONT}">'
            f'<tr><td style="padding:24px 20px 18px;background:{SHELL}">'
            f'<div style="font-size:24px;line-height:30px;font-weight:700;color:#ffffff">Job radar · {html.escape(name)}</div>'
            f'<div style="margin-top:2px;font-size:13px;line-height:18px;color:#9ec5f4">{date.today():%A %d %B %Y}</div>'
            f'<div style="margin:14px 0 12px;font-size:16px;line-height:22px;font-weight:600;color:#ffffff">'
            f'{html.escape(summary)}</div>{chips}</td></tr>')
    foot = (f'<tr><td style="padding:18px 20px 22px;border-top:1px solid {LINE};font-size:12px;line-height:18px;'
            f'color:{INK2}">Entry and junior jobs first, senior last; in each, your companies first, then the best matches. '
            f'Each job is sent once.<br>On your laptop: the <a href="http://127.0.0.1:8501" style="color:{LINK}">'
            f'tracker</a> to mark what you apply to · <a href="http://127.0.0.1:8081" style="color:{LINK}">Airflow'
            f'</a> collects at {times(SCHEDULE["collect"])} · {HOME} email {times(SCHEDULE["home_email"])}, '
            f'outside {HOME} {times(SCHEDULE["abroad_email"])} (Cairo time).</td></tr></table></div>')
    # everything in reading order until EMAIL_BYTES (UTF-8, the header and footer counted), then one
    # line for the jobs left to the tracker
    sections, size = [], len((head + foot).encode())
    for part, _ in parts:
        size += len(part.encode())
        if size > EMAIL_BYTES:
            break
        sections.append(part)
    left = sum(card for _, card in parts[len(sections):])
    if left:
        sections.append(f'<tr><td style="padding:14px 20px;border-top:1px solid {LINE};font-size:13px;color:{INK2}">'
                        f'+ {left} more job{"s" * (left != 1)} {more}</td></tr>')
    return head + "".join(sections) + foot


def times(items: list) -> str:
    """["1am", "7am", "6pm"] -> "1am, 7am and 6pm"."""
    return " and ".join([", ".join(items[:-1]), items[-1]] if len(items) > 1 else items)


def send(body: str, title: str, name: str) -> bool:
    """Email the digest; without Gmail settings, write it to output/ instead (one file per email and
    time, so the two emails do not overwrite each other) and return False."""
    user, password = os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASSWORD")
    if not (user and password):
        (ROOT / "output").mkdir(exist_ok=True)
        path = ROOT / "output" / f"digest-{datetime.now():%Y-%m-%d-%H%M}-{name.lower().replace(' ', '-')}.html"
        path.write_text(body, encoding="utf-8")
        print(f"email: no Gmail settings, wrote {path}")
        return False
    msg = EmailMessage()
    msg["Subject"] = title
    msg["From"] = user
    msg["To"] = os.environ.get("MAIL_TO") or user
    msg.set_content(f"{title}. Open this email as HTML to see them.")
    msg.add_alternative(body, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(user, password.replace(" ", ""))
        smtp.send_message(msg)
    print(f"email: sent '{title}' to {msg['To']}")
    return True
