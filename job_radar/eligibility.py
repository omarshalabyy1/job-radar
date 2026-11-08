"""Whether someone living in Cairo, Egypt can legally take a job (features.eligibility), in two places:

1. not_for_egypt, read from the posting alone, at every extract (steps.load): a posting it bars is never stored. The
   board's own "who can apply" field decides alone (BOARDS, Wellfound, freehire's countries): open to anywhere, EMEA,
   MENA, Africa, the Middle East, Egypt or a time zone that covers Cairo passes, any other list ("USA", "Europe") bars
   it. Without that field, a remote job its location ties to the USA, Canada or Europe is barred.
2. the eligibility step, after match_skills, for what only a description shows: every stored job outside ai_places with
   no board field and a description gets one Claude Haiku call: yes, no or unclear, with a short reason (ai_places'
   jobs are judged by grade_claude, which asks Sonnet the same). A 'no' is the job's restriction (core.job.restriction),
   which hides it in the tracker and the emails (config.hidden_reason); yes and unclear hide nothing.

Each job is judged once (core.job.eligibility_at); one with no description yet waits for one. With features.eligibility
false neither runs; with no ANTHROPIC_API_KEY the step warns and judges nothing. When jobs were selected for Claude and
none was judged (key, credit or network) it fails, so the task is red; the next run tries them again."""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel

from . import claude
from .config import AI_PLACES, BAYT_PLACES, FEATURES, WORK_DAYS, place_of
from .sources import time_left

MODEL = "claude-haiku-5-5"
SECONDS = 180  # the DAG gives the task 240 s; the jobs left are judged next run
MAX_TOKENS = 1024  # the answer is a word and a phrase; the rest is room for its thinking at low effort
# The boards whose location is their own "who can apply" field (sources/remote.py writes it there): Himalayas'
# locationRestrictions, Remotive's candidate_required_location, Jobicy's jobGeo, Remote.co's countries, We Work
# Remotely's region, the country pill of Daily Remote and Relomote, Working Nomads' and Remote OK's location (not when Remote
# OK's is blank or just "Remote": its page shows Worldwide then, but no one chose it, so the description decides).
# Welcome to the Jungle names its offices and Arbeitnow a city: where the company is, not who can apply.
BOARDS = {"himalayas", "remotive", "jobicy", "remoteco", "weworkremotely", "dailyremote", "relomote", "workingnomads",
          "remoteok"}
# open to someone in Cairo: Africa, but not South Africa alone; EET is Cairo's own time zone
OPEN = re.compile(r"worldwide|anywhere|global|\bemea\b|\bmena\b|(?<!south )\bafrica|middle east|egypt|\bees?t\b", re.I)
# a posting that says it takes someone in Egypt
OPEN_TEXT = re.compile(r"worldwide|anywhere in the world|work from anywhere|\begypt\b|\bemea\b|\bmena\b", re.I)
OFFSET = re.compile(r"\b(?:utc|gmt)\s*([+−–-])\s*(\d{1,2})", re.I)


class Verdict(BaseModel):
    answer: Literal["yes", "no", "unclear"]
    reason: str


SYSTEM = ("You read one job posting for a candidate who lives in Cairo, Egypt, holds only Egyptian citizenship and has no "
          "work permit, residence or security clearance anywhere else. answer: yes when they can legally take the job "
          "from Cairo; no when the posting requires something they cannot meet (another country's citizenship, work "
          "authorization or residence, living or being located in another country or region, a security clearance, "
          "nationals of a country only, fluency in a language other than Arabic or English); unclear when the posting does not say. Where the company or office is, a "
          "visa it will not sponsor, or a time zone to overlap is not a bar by itself. reason: a short phrase, the "
          "posting's own words when it says no.")


def who_can_apply(source: str, location: str, payload: dict) -> str | None:
    """The board's own "who can apply" field for this job, or None when the board has none."""
    if source == "wellfound":  # its accepted remote locations; locationNames are its offices
        return ", ".join(payload.get("acceptedRemoteLocationNames") or []) or None
    if source == "freehire":  # it gives the countries (else the regions) a job is open to; its location text is only where it is
        countries = [c.upper() for c in payload.get("countries") or []]
        if "EG" in countries:
            return "Egypt"
        text = ", ".join(countries or [r.replace("_", " ") for r in payload.get("regions") or []])
        if not text and payload.get("source") == "himalayas":
            return "Himalayas gives no region"  # a Himalayas job with no region is not one its own API lists as open to Egypt
        return text or None
    if source == "remoteok" and re.fullmatch(r"\s*(remote)?\s*", payload.get("location") or "", re.I):
        return None  # its page says "Worldwide" for a blank location, but nobody chose that: the description decides
    return location if source in BOARDS and location else None


def board_restriction(where: str) -> str | None:
    """None when a board's "who can apply" field lets someone in Cairo apply, else what it limits the job to:
    "Worldwide", "EMEA", "Egypt, Nigeria" or "UTC-1 to UTC+3" pass; "United States" or "Europe" give
    "open to United States only"."""
    where = re.sub(r"^remote:\s*|\s*\((remote|hybrid|on-?site)\)$", "", where.strip(), flags=re.I)
    if OPEN.search(where) or where.lower() in ("", "remote", "hybrid", "onsite", "on-site"):  # no region named: no bar
        return None
    offsets = [int(n) * (1 if sign == "+" else -1) for sign, n in OFFSET.findall(where)]
    if offsets and min(offsets) <= 3 and max(offsets) >= 2:  # one zone or a range that takes in Cairo's +2 or +3
        return None
    return f"open to {where[:120]} only"


def tied_elsewhere(place: str, location: str) -> str | None:
    """'open to USA only' for a remote job tied to a place outside Egypt and the Gulf (the place its location names, else
    the place searched), unless the location itself says worldwide, EMEA, MENA or Egypt. A description's "worldwide" is
    not read: it is usually about customers. None for the rest, Gulf jobs included: a Gulf company may take someone in
    Egypt, so the description decides those."""
    tied = place_of(location or "")
    if tied == "Remote":
        return None
    where = tied or place
    if where in BAYT_PLACES or where in ("Remote", "Unknown location") or OPEN_TEXT.search(location or ""):
        return None
    return f"open to {where} only"


def not_for_egypt(source: str, location: str, place: str, payload: dict) -> str | None:
    """Why someone living in Cairo cannot take a posting, known from the posting alone (no description, no Claude): the
    board's own "who can apply" field decides alone, else the place the job is tied to. None when it may be taken or
    nothing says otherwise. Your LinkedIn alerts are never judged here: every job in one is kept. steps.load drops a
    posting this bars before it is stored."""
    if source in ("email", "linkedin"):
        return None
    where = who_can_apply(source, location or "", payload or {})
    return board_restriction(where) if where else tied_elsewhere(place, location or "")


def eligibility(conn) -> None:
    if not FEATURES["eligibility"]:
        print("eligibility: off in settings.yaml (features.eligibility)")
        return
    jobs = conn.execute(
        "SELECT j.job_id, j.title, j.company, j.location, j.place, j.source, j.description, r.payload FROM core.job j"
        " LEFT JOIN raw.job_posting r ON (r.source, r.job_url) = (j.source, j.job_url)"
        " WHERE j.in_scope AND j.eligibility_at IS NULL AND j.first_seen >= current_date - %s ORDER BY j.job_id", (WORK_DAYS,)).fetchall()
    judged, ask = [], []  # (restriction, job_id); the jobs for Claude
    for job_id, title, company, location, place, source, description, payload in jobs:
        if who_can_apply(source, location, payload or {}):  # its board said who can apply, and the extract kept it
            judged.append((None, job_id))
        elif place in AI_PLACES:  # grade_claude asks Sonnet
            judged.append((None, job_id))
        elif description:
            ask.append((job_id, title, company, location, description))
    boards = len(judged)
    if ask and not os.environ.get("ANTHROPIC_API_KEY"):
        print(f"WARNING eligibility: no ANTHROPIC_API_KEY in .env, {len(ask)} jobs without a board field left unjudged")
        ask = []
    out_of_credit = out_of_time = False
    if ask:
        import anthropic  # for its errors; the SDK loads only in a run that calls Claude
        client = claude.client()

        def judge(job: tuple) -> tuple | None:
            nonlocal out_of_credit, out_of_time
            job_id, title, company, location, description = job
            if out_of_credit or out_of_time:
                return None
            if time_left(SECONDS) < 20:  # slow, not broken: the jobs left are judged next run
                out_of_time = True
                return None
            try:
                r = client.messages.parse(
                    model=MODEL, max_tokens=MAX_TOKENS, system=SYSTEM, output_format=Verdict,
                    output_config={"effort": "low"},
                    messages=[{"role": "user", "content": f"{title} at {company} ({location})\n\n{description[:6000]}"}])
            except anthropic.BadRequestError as e:
                if claude.out_of_credit(e):
                    out_of_credit = True
                else:
                    print(f"WARNING eligibility: job {job_id} refused: {e!r}"[:300])
                return None
            except anthropic.APIError as e:  # one job down is a warning: it is judged next run
                print(f"WARNING eligibility: job {job_id}: {e!r}"[:300])
                return None
            v = r.parsed_output
            if v is None:
                return None
            return (" ".join(v.reason.split())[:200] or "not open to Egypt") if v.answer == "no" else None, job_id

        with ThreadPoolExecutor(4) as pool:
            judged += [row for row in pool.map(judge, ask) if row]
    with conn.cursor() as cur:
        cur.executemany("UPDATE core.job SET restriction = %s, eligibility_at = now() WHERE job_id = %s", judged)
    conn.commit()
    print(f"eligibility: {boards} jobs judged by their board or left to grade_claude, {len(judged) - boards} of {len(ask)}"
          f" by {MODEL}; {sum(r is not None for r, _ in judged)} barred to someone in Egypt (hidden)"
          + (" - WARNING the API credit ran out" if out_of_credit else ""))
    if ask and len(judged) == boards and not out_of_time:
        raise RuntimeError(f"eligibility: 0 of {len(ask)} jobs judged by {MODEL} - check the API key, credit and network")
