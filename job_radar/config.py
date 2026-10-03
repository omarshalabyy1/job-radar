"""What job_radar looks for, from settings.yaml: the roles in rank order, the places, your companies."""

import re
from pathlib import Path

import yaml

HOURS_OLD = 24  # every run looks back this far: a run after the laptop was asleep or off backfills the last day
# A whole run stays under 300 seconds: each extract step (they run side by side) gets 150 seconds
# and describe 60; what does not fit waits for the next run.
EXTRACT_SECONDS = 150
DESCRIBE_SECONDS = 60

ROOT = Path(__file__).resolve().parent.parent  # the repo
# What you look for, where and when live in settings.yaml at the repo root, as plain words; this
# module turns them into the rules below. What stays here is plumbing, not choices.
SETTINGS = yaml.safe_load((ROOT / "settings.yaml").read_text(encoding="utf-8"))


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
# a free-text location -> place, for the sources that do not search by place (first match wins)
PLACE_PATTERNS = {place["name"]: words(place["words"]) + (f"|{US_CODES}" if place["name"] == "USA" else "")
                  for place in SETTINGS["places"]}
if HOME not in PLACE_PATTERNS:  # the Egypt email and the onsite rule go by this name
    raise ValueError(f"settings.yaml: keep a place named {HOME!r} in places")
REMOTE_OPEN_TO = r"worldwide|anywhere|global|emea|mena|africa|middle east|egypt"
# Your rule: an onsite or hybrid job only at home, in onsite_areas; anywhere else only remote. A home
# job is dropped only when its location names another home city (no city: kept).
CAIRO_GIZA = words(SETTINGS["onsite_areas"])
OTHER_EGYPT = words(SETTINGS["other_home_cities"])
REMOTE = words(SETTINGS["remote_words"])
# a location or title saying one of these is not a remote job, whatever else it says
NOT_REMOTE = r"hybrid|on-?site|in[- ]office|\b(not|non|no)[- ]remote"
# so the boards (Indeed, Bayt, Workable) search every other place for remote jobs only, and a site
# with no remote filter is searched in these places only
ONSITE_PLACES = {HOME}
EXPERIENCE = SETTINGS["experience"]
SCHEDULE = SETTINGS["schedule"]
# job pages never fetched: LinkedIn (its jobs come only from your alert emails)
NO_FETCH = r"linkedin\.com"
# Your choice (2026-10-03): read public careers pages even when their robots.txt asks crawlers to
# stay away (a few requests a run, no login). Pages behind a bot check are never read either way.
RESPECT_ROBOTS = False

# companies whose jobs are starred and listed first, from any source (with every company in core.company)
TARGET_COMPANIES = words(SETTINGS["your_companies"])

# The companies whose own career sites are read: settings.yaml (companies), copied to core.company by
# the schema step. A Phenom site searches by keywords only, so each search names a place: Egypt, or
# remote.
PHENOM_SEARCHES = [f"{kw} {where}" for kw in ("data", "AI", "analyst") for where in ("Egypt", "remote")]

# Tanqeeb gathers Wuzzuf, Bayt, Forasna, NaukriGulf, GulfTalent ...: its country sites and the job
# pages its robots.txt allows (no ?keywords searches). It has no remote filter, so Egypt only.
TANQEEB_SITES = {"egypt": "Egypt"}
TANQEEB_PAGES = ["it-jobs", "data-analyst-jobs", "business-analyst-jobs", "python-developer-jobs", "internship-jobs"]


def role_of(title: str) -> int | None:
    """The rank of the first role whose patterns all match the title, seniority aside."""
    if re.search(NOT_RELEVANT, title, re.I):
        return None
    for rank, _, _, patterns in ROLES:
        if all(re.search(p, title, re.I) for p in patterns):
            return rank
    return None


def too_senior(title: str) -> bool:
    return re.search(TOO_SENIOR, title, re.I) is not None


def place_of(location: str) -> str | None:
    """Your home, a Gulf country, USA, Europe, Remote (open to Egypt), or None when out of scope. A
    remote job open worldwide is Remote; one tied to a country takes that country."""
    if re.search(r"remote", location, re.I) and re.search(r"worldwide|anywhere|global", location, re.I):
        return "Remote"
    for place, pattern in PLACE_PATTERNS.items():
        if re.search(pattern, location, re.I):
            return place
    # a home area or city alone ("Maadi", "Sheikh Zayed", "القاهرة"); after the other places, so
    # "Sheikh Zayed Road, Dubai" stays in the UAE
    if re.search(f"{CAIRO_GIZA}|{OTHER_EGYPT}", location, re.I):
        return HOME
    if re.search(r"remote", location, re.I) and re.search(REMOTE_OPEN_TO, location, re.I):
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
