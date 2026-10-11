"""What job_radar looks for, from settings.yaml: the roles in rank order, the places, your companies."""

import functools
import os
import re
from pathlib import Path

import yaml
from dotenv import load_dotenv

HOURS_OLD = 24  # every run looks back this far: a run after the laptop was asleep or off backfills the last day
KEEP_DAYS = 4  # a job first seen longer ago is deleted (transform), unless you noted or applied to it
# Each extract step (Airflow runs them one by one) gets 150 seconds and describe 60; what does not
# fit waits for the next run.
EXTRACT_SECONDS = 150
DESCRIBE_SECONDS = 60

ROOT = Path(__file__).resolve().parent.parent  # the repo
load_dotenv(ROOT / ".env")  # so OUTPUT below sees .env however the code is started
# the one folder for drafts, sessions, waits and digests: set JOB_RADAR_OUTPUT to put it on the E: drive
OUTPUT = Path(os.environ.get("JOB_RADAR_OUTPUT") or ROOT / "output")
# What you look for, where and when live in settings.yaml at the repo root, as plain words; this
# module turns them into the rules below. What stays here is plumbing, not choices.
SETTINGS = yaml.safe_load((ROOT / "settings.yaml").read_text(encoding="utf-8"))
FEATURES = SETTINGS["features"]  # each smart feature on or off, read at run time
AI_PLACES = SETTINGS["ai_places"]  # the AI features work on the jobs in these places only


def words(items: list) -> str:
    """One pattern for a list of words: each a whole word, any case; a trailing * matches the start
    of a word ("engineer*": engineer, engineers, engineering)."""
    return "|".join(rf"\b{re.escape(str(w)[:-1])}" if str(w).endswith("*") else rf"\b{re.escape(str(w))}\b"
                    for w in items) or r"(?!)"


HOME = "Egypt"  # onsite and hybrid jobs only here, and its own email
# (rank, label, Indeed/Bayt search term, title patterns that must all match), in your order
ROLES = [(rank, role["name"], " OR ".join(f'"{term}"' for term in role["search"]),
          [words(need) for need in role["title_needs"]]) for rank, role in enumerate(SETTINGS["roles"], 1)]
ROLE_LABEL = {rank: label for rank, label, _, _ in ROLES}
# a job in a LinkedIn alert whose title is none of your roles: LinkedIn chose it for your alert, so it
# is kept, listed after your roles
ALERT_RANK = len(ROLES) + 1
ROLE_LABEL[ALERT_RANK] = "More from your LinkedIn alerts"
# plain keywords for the sources that take no OR syntax (Himalayas, Workable, Jooble)
KEYWORDS = SETTINGS["search_words"]
# entry, mid and senior are yours; above senior is not
TOO_SENIOR = words(SETTINGS["too_senior"])
# titles that are never yours, whatever else they say
NOT_RELEVANT = words(SETTINGS["never"])

# (place, search location, Indeed country)
PLACES = [(place["name"], location, country) for place in SETTINGS["places"]
          for location, country in place["search"].items()]
BAYT_PLACES = {"Egypt", "UAE", "Saudi Arabia", "Qatar", "Kuwait", "Bahrain", "Oman"}  # where Bayt has jobs
# The USA's place also takes "US" or "USA" in capitals, or a state code at the end that is not after
# another code ("Toronto, ON, CA" is Canada); IN and DE are left out (India, Germany).
US_CODES = (r"(?-i:\bUSA?\b)|(?-i:(?<!, [A-Z]{2}), (AL|AK|AZ|AR|CA|CO|CT|FL|GA|HI|ID|IL|IA|KS|KY|LA|ME|MD|MA|MI|MN"
            r"|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)$)")
# a free-text location -> place, for the sources that do not search by place (first match wins);
# compiled once, as place_of runs on every posting
PLACE_PATTERNS = {place["name"]: re.compile(words(place["words"]) + (f"|{US_CODES}" if place["name"] == "USA" else ""),
                                            re.I)
                  for place in SETTINGS["places"]}
if HOME not in PLACE_PATTERNS:  # the Egypt email and the onsite rule go by this name
    raise ValueError(f"settings.yaml: keep a place named {HOME!r} in places")
REMOTE_OPEN_TO = r"worldwide|anywhere|global|emea|mena|africa|middle east|egypt"
# Your rule: an onsite or hybrid job only at home, in onsite_areas; anywhere else only remote. A home
# job is dropped only when its location names another home city (no city: kept).
CAIRO_GIZA = words(SETTINGS["onsite_areas"])
OTHER_EGYPT = words(SETTINGS["other_home_cities"])
HOME_AREAS = re.compile(f"{CAIRO_GIZA}|{OTHER_EGYPT}", re.I)  # any of them names home, for place_of
REMOTE = words(SETTINGS["remote_words"])
# a location or title saying one of these is not a remote job, whatever else it says
NOT_REMOTE = r"hybrid|on-?site|in[- ]office|\b(not|non|no)[- ]remote"
# so the boards (Indeed, Bayt, Workable) search every other place for remote jobs only, and a site
# with no remote filter is searched in these places only
ONSITE_PLACES = {HOME}
EXPERIENCE = SETTINGS["experience"]
SCHEDULE = SETTINGS["schedule"]
# job pages never fetched: LinkedIn (its jobs come only from your alert emails) and Daily Remote (290 pages tried,
# 0 descriptions found, 2026-10-11: it hides them)
NO_FETCH = r"linkedin\.com|dailyremote\.com"

# companies whose jobs are starred and listed first, from any source (with every company in core.company)
TARGET_COMPANIES = words(SETTINGS["your_companies"])

# The companies whose own career sites are read: settings.yaml (companies), copied to core.company by
# the schema step; only their jobs in Egypt are kept (your rule, 2026-10-05). A Phenom site searches by
# keywords only, so each search names Egypt.
PHENOM_SEARCHES = [f"{kw} Egypt" for kw in ("data", "AI", "analyst")]

# Tanqeeb gathers Wuzzuf, Bayt, Forasna, NaukriGulf, GulfTalent ...: its country sites and its job
# pages by field (its keyword search shows a script no jobs, 2026-10-05). It has no remote filter:
# outside Egypt a job is kept only when its title or place says remote (your rule).
TANQEEB_SITES = {"egypt": "Egypt", "uae": "UAE", "saudi": "Saudi Arabia", "qatar": "Qatar", "kuwait": "Kuwait",
                 "bahrain": "Bahrain", "oman": "Oman"}
TANQEEB_PAGES = ["it-jobs", "data-analyst-jobs", "business-analyst-jobs", "python-developer-jobs", "internship-jobs"]

# Relomote's pages of remote jobs open to someone in Egypt, by field (its robots.txt allows them)
RELOMOTE_PAGES = ["data-analytics-in-egypt", "engineering-development-in-egypt"]


def role_of(title: str, alert: bool = False) -> int | None:
    """The rank of the first role whose patterns all match the title, seniority aside; for a job in
    a LinkedIn alert (alert) that matches none, ALERT_RANK. A title never yours has no rank."""
    if not title.strip() or re.search(NOT_RELEVANT, title, re.I):
        return None
    for rank, _, _, patterns in ROLES:
        if all(re.search(p, title, re.I) for p in patterns):
            return rank
    return ALERT_RANK if alert else None


def too_senior(title: str) -> bool:
    return re.search(TOO_SENIOR, title, re.I) is not None


@functools.cache  # locations repeat ("Remote", "Cairo, Egypt"): each is worked out once per step
def place_of(location: str) -> str | None:
    """Your home, a Gulf country, USA, Europe, Remote (open to Egypt), or None when out of scope. A
    remote job open worldwide is Remote; one tied to a country takes that country."""
    remote = re.search(r"remote", location, re.I)
    if remote and re.search(r"worldwide|anywhere|global", location, re.I):
        return "Remote"
    for place, pattern in PLACE_PATTERNS.items():
        if pattern.search(location):
            return place
    # a home area or city alone ("Maadi", "Sheikh Zayed", "القاهرة"); after the other places, so
    # "Sheikh Zayed Road, Dubai" stays in the UAE
    if HOME_AREAS.search(location):
        return HOME
    if remote and re.search(REMOTE_OPEN_TO, location, re.I):
        return "Remote"
    return None


def in_reach(place: str, location: str, title: str, remote: bool = False, description: str = "") -> bool:
    """A remote job in any place; an onsite or hybrid one only at home, in onsite_areas. Outside
    home, and where the place is unknown, only remote: no hybrid, no onsite, no "not remote".
    remote: the board says so (JobSpy's is_remote), even when the location names only a city; JobSpy
    also marks Indeed's "Hybrid remote" jobs remote, so then the description must not say hybrid."""
    text = f"{location} {title}"
    if not re.search(NOT_REMOTE, text, re.I) and (
            place == "Remote" or re.search(REMOTE, text, re.I)
            or (remote and not re.search(r"\bhybride?\b", description, re.I))):
        return True
    return place == HOME and (re.search(CAIRO_GIZA, location, re.I) is not None
                              or re.search(OTHER_EGYPT, location, re.I) is None)


def is_target(company: str) -> bool:
    return re.search(TARGET_COMPANIES, company, re.I) is not None


# Where a posting names a country, a bloc or a nationality that someone living in Egypt cannot meet.
# "Egyptian nationals", "based in Egypt", "no visa sponsorship", "offices in the US" and "US clients"
# are not restrictions and match nothing here. US, UK, EU and KSA only count in capitals ("for us only").
# restriction() writes U.S., U.S.A. and U.K. as US, USA and UK first, so these never see a dot.
_COUNTRY = (r"(?:(?-i:USA?|UK|EU)|United States|America|United Kingdom|Britain|Canada|Europe|"
            r"European Union|Australia)(?!\w)")
_NATION = r"(?:(?-i:USA?\.?|UK|EU)|American|British|Canadian|European|Australian)"  # "US. citizenship" is a typo seen live
_GULF = r"(?:GCC|Saudi|Emirati|UAE|Qatari|Kuwaiti|Omani|Bahraini)"
RESTRICTION = re.compile("|".join([
    rf"{_NATION}\s+citizen(?:s|ship)?\b",
    # a clearance "preferred" or "an advantage" is no bar, nor is a form field "Security Clearance: None"
    # (no \b after None: "Requirement:** NoneThis position")
    r"(?:TS/SCI|(?:(?-i:SC)|security|secret|top[- ]secret)\s+clearance\b|public\s+trust\b)"
    r"(?!(?:\s+requirements?)?\W{0,8}(?:none|n/a))"
    r"(?![^.\n]{0,60}\b(?:preferred|advantag\w+|a\s+plus|desirable|beneficial|nice\s+to\s+have|bonus)\b)",
    rf"(?:authori[sz]ed|eligible|legally\s+(?:able|entitled|permitted))\s+to\s+work\s+in\s+(?:the\s+)?{_COUNTRY}",
    rf"right\s+to\s+work\s+in\s+(?:the\s+)?{_COUNTRY}",
    rf"must\s+(?:be\s+)?(?:(?:located|based|living|resident)\s+in|reside\s+in|(?:a\s+)?resident\s+of)"
    rf"\s+(?:the\s+)?{_COUNTRY}",
    rf"{_COUNTRY}[- ]based\s+(?:candidates?\s+)?only\b",
    r"(?-i:\b(?:US|USA|UK|KSA|EU))\s+only\b",
    rf"{_GULF}\s+(?:nationals?|citizens?)\s+only\b",
    r"Saudi[sz]ation",
]), re.I)
# a clause that says the thing is not asked for: "US citizenship is not required", "No security clearance
# required", "You do not need to be authorized to work in the US", "The employer does not require a clearance",
# "Nor will we require US citizenship"
NOT_ASKED = re.compile(r"\b(?:not|isn['’]t|aren['’]t)\s+(?:required|needed|necessary|mandatory|a\s+requirement)\b"
                       r"|\bno\b[^.;\n]{0,50}\b(?:required|needed)\b|\b(?:do|does)(?:\s+not|n['’]t)\s+(?:need|require)\b"
                       r"|\bnor\s+will\b[^.;\n]{0,60}\brequire\b", re.I)


# where a sentence ends, so a "not required" cancels only its own sentence: a full stop, semicolon or new line
SENTENCE_BREAK = re.compile(r"[.;\n]")
DOTTED = re.compile(r"\bU\.([SK])\b\.?(?:(A)\b\.?)?")  # U.S., U.S.A., U.K. (a last dot missing too): not sentence ends


def restriction(text: str | None) -> str | None:
    """The phrase that bars someone living in Egypt (a citizenship, a clearance, a right to work or a
    residence in the US, UK, Canada, EU or Australia; Gulf nationals only), or None; U.S., U.S.A. and U.K. are
    read, and come back, as US, USA and UK. A phrase in a sentence that says it is not required or needed
    ("US citizenship is not required") bars nobody; the next sentence ("No clearance required for the first
    round. Must be a US citizen.") still counts. A flag hides a job (the tracker's Show hidden jobs brings it
    back) and a missed flag leaves it visible, so a negation anywhere in the sentence cancels the flag, even
    "US citizenship is required, clearance is not required" (an accepted miss). A benefit line "(US only)" is
    flagged although it bars nobody (a known false flag: the job is hidden, not lost)."""
    text = DOTTED.sub(lambda d: f"U{d[1]}{d[2] or ''}", text or "")
    for m in RESTRICTION.finditer(text):
        start = max((b.end() for b in SENTENCE_BREAK.finditer(text, 0, m.start())), default=0)
        after = SENTENCE_BREAK.search(text, m.end())
        if not NOT_ASKED.search(text[start:after.start() if after else len(text)]):
            return " ".join(m.group(0).split())
    return None


def hidden_reason(status: str, restriction: str | None, score, features: dict = FEATURES) -> str | None:
    """Why the email and the tracker leave a job out, as the switches stand now, or None: only a job with no
    status of yours ('new'). Its restriction hides it with features.eligibility alone; 'score N' (3 or lower)
    hides it with features.auto_hide and features.grade both on. Read at run time, so a switch turned off stops
    hiding on the next run and turned on hides again; the facts themselves (mart.job_status) are never changed."""
    if status != "new":
        return None
    if isinstance(restriction, str) and restriction and features["eligibility"]:  # pandas gives NaN for none
        return restriction
    if features["auto_hide"] and features["grade"] and score is not None and score == score and score <= 3:  # score == score: not NaN
        return f"score {int(score)}"
    return None
