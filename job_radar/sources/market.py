"""The website's wide collection (features.market): every tech job on a company's own board (Greenhouse, Lever, Ashby,
SmartRecruiters, Workable, Recruitee, Personio), any role, level and country, through each platform's public list of
open jobs. Each list is the board's whole list, read again every run, so a job it stops listing is closed (transform).
The boards are core.board (extract_market chooses them); boards.csv is the first list (from Radarak, MIT, see
LICENSE-THIRD-PARTY). A posting's source is '<platform>-board' and its payload names its board ('greenhouse:stripe');
its text is kept as the description, not in the payload, to keep raw.job_posting small."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests
from bs4 import BeautifulSoup

from ..tech import is_tech_role
from .base import fetch, get, row

TEXT_CHARS = 20_000  # a description longer than this is cut: the skills are named early, and the warehouse stays small
FIELD_CHARS = 1_000  # a payload field larger than this (the text, translations, questions) is left out of raw.job_posting


def text(html: str | None) -> str | None:
    return (BeautifulSoup(html, "html.parser").get_text(" ", strip=True)[:TEXT_CHARS] or None) if html else None


def posting(ats: str, token: str, title, company, location, url, posted, description, payload: dict) -> dict:
    slim = {k: v for k, v in payload.items() if len(json.dumps(v, default=str)) <= FIELD_CHARS}
    return row(f"{ats}-board", None, (title or "").strip(), company, location, url, (posted or "")[:10] or None,
               description, {**slim, "board": f"{ats}:{token}"})


def where(place: str | None, remote: bool = False, hybrid: bool = False) -> str:
    """A location with how the job is done, as job-radar's location words read it ('Berlin (hybrid)')."""
    return (place or "").strip() + (" (remote)" if remote else " (hybrid)" if hybrid else "")


def greenhouse(token: str, company: str) -> list[dict]:
    data = get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", content="true").json()
    return [posting("greenhouse", token, j["title"], j.get("company_name") or company, (j.get("location") or {}).get("name", ""),
                    j["absolute_url"], j.get("first_published") or j.get("updated_at"), text(j.get("content")), j)
            for j in data.get("jobs") or [] if is_tech_role(j.get("title"))]


def lever(token: str, company: str) -> list[dict]:
    try:
        jobs = get(f"https://api.lever.co/v0/postings/{token}", mode="json").json()
    except requests.HTTPError as e:  # a company is on Lever's global or its EU instance, not both
        if e.response is None or e.response.status_code != 404:
            raise
        jobs = get(f"https://api.eu.lever.co/v0/postings/{token}", mode="json").json()
    rows = []
    for j in jobs if isinstance(jobs, list) else []:
        if not is_tech_role(j.get("text")):
            continue
        pay = j.get("salaryRange") or {}
        if pay.get("currency"):  # as salary() reads it: 'per-year-salary' -> 'year'
            j = {**j, "salaryMin": pay.get("min"), "salaryMax": pay.get("max"), "salaryCurrency": pay["currency"],
                 "salaryPeriod": (pay.get("interval") or "").replace("per-", "").split("-")[0] or None}
        mode = (j.get("workplaceType") or "").lower()
        posted = str(datetime.fromtimestamp(j["createdAt"] / 1000, timezone.utc).date()) if j.get("createdAt") else None
        rows.append(posting("lever", token, j["text"], company, where((j.get("categories") or {}).get("location"),
                            mode == "remote", mode == "hybrid"), j["hostedUrl"], posted,
                            " ".join(filter(None, [j.get("descriptionPlain"), *(f"{x.get('text', '')}: {text(x.get('content'))}"
                                                   for x in j.get("lists") or []), j.get("additionalPlain")]))[:TEXT_CHARS] or None, j))
    return rows


def ashby(token: str, company: str) -> list[dict]:
    data = get(f"https://api.ashbyhq.com/posting-api/job-board/{token}").json()
    return [posting("ashby", token, j["title"], company, where(j.get("location"), j.get("isRemote") is True,
                    (j.get("workplaceType") or "").lower() == "hybrid"), j["jobUrl"], j.get("publishedAt"),
                    (j.get("descriptionPlain") or "")[:TEXT_CHARS] or None, j)
            for j in data.get("jobs") or [] if j.get("isListed", True) and is_tech_role(j.get("title"))]


def smartrecruiters(token: str, company: str) -> list[dict]:
    """Its list names no description (describe does not read website jobs): their skills come from the title."""
    rows = []
    for offset in range(0, 500, 100):  # the newest 500: a group with thousands of openings is read in part
        page = get(f"https://api.smartrecruiters.com/v1/companies/{token}/postings", limit=100, offset=offset).json()
        for j in page.get("content") or []:
            spot = j.get("location") or {}
            if is_tech_role(j.get("name")):
                rows.append(posting("smartrecruiters", token, j["name"], (j.get("company") or {}).get("name") or company,
                                    where(spot.get("fullLocation") or spot.get("city"), spot.get("remote") is True,
                                          spot.get("hybrid") is True),
                                    f"https://jobs.smartrecruiters.com/{token}/{j['id']}", j.get("releasedDate"), None, j))
        if offset + 100 >= (page.get("totalFound") or 0):
            break
    return rows


def workable(token: str, company: str) -> list[dict]:
    data = get(f"https://apply.workable.com/api/v1/widget/accounts/{token}", details="true").json()
    return [posting("workable", token, j["title"], data.get("name") or company,
                    where(", ".join(filter(None, [j.get("city"), j.get("country")])), j.get("telecommuting") is True),
                    j.get("url") or j.get("application_url"), j.get("published_on"), text(j.get("description")), j)
            for j in data.get("jobs") or [] if is_tech_role(j.get("title")) and (j.get("url") or j.get("application_url"))]


def recruitee(token: str, company: str) -> list[dict]:
    data = get(f"https://{token}.recruitee.com/api/offers/").json()
    return [posting("recruitee", token, j["title"], j.get("company_name") or company,
                    where(j.get("location") or ", ".join(filter(None, [j.get("city"), j.get("country")])),
                          j.get("remote") is True, j.get("hybrid") is True),
                    j["careers_url"], j.get("published_at"), text(j.get("description")), j)
            for j in data.get("offers") or [] if is_tech_role(j.get("title")) and j.get("careers_url")]


def personio(token: str, company: str) -> list[dict]:
    rows = []
    for position in ET.fromstring(get(f"https://{token}.jobs.personio.de/xml").content).iter("position"):
        title = position.findtext("name", "").strip()
        if not is_tech_role(title):
            continue
        offices = ", ".join(dict.fromkeys(o.text.strip() for o in position.iter("office") if o.text and o.text.strip()))
        body = " ".join(text(d.findtext("value", "")) or "" for d in position.iter("jobDescription")).strip()
        rows.append(posting("personio", token, title, position.findtext("subcompany") or company, offices,
                            f"https://{token}.jobs.personio.de/job/{position.findtext('id')}", position.findtext("createdAt"),
                            body[:TEXT_CHARS] or None, {child.tag: child.text for child in position if len(child) == 0}))
    return rows


def epoch_day(value) -> str | None:
    """A date from an epoch in seconds ('1760000000') or an ISO text; None when neither."""
    if str(value or "").isdigit():
        return str(datetime.fromtimestamp(int(value), timezone.utc).date())
    return str(value)[:10] or None if value else None


def feed(source: str, jobs: list, title, company, where_, url, posted, description, remote: bool = True) -> list[dict]:
    """A remote board's whole feed as wide postings (the field names are the board's); a remote board's job is remote."""
    return [row(source, None, j[title].strip(), j.get(company), where(j.get(where_) or "Worldwide", remote), j[url],
                epoch_day(j.get(posted)), text(j.get(description)), {k: v for k, v in j.items() if k != description})
            for j in jobs if j.get(title) and j.get(url) and is_tech_role(j[title])]


# Jobicy's industries and keyword searches that cover the tech jobs (Radarak's: its terms allow polling hourly)
JOBICY_INDUSTRIES = ["data-science", "engineering", "design-multimedia"]
JOBICY_TAGS = ["data engineer", "data analyst", "analytics", "machine learning", "devops", "android"]  # it refuses 'qa' (400)


def remote_apis() -> list[dict]:
    """The remote boards' public APIs, every tech job they list (job-radar's own readers keep only your roles): Jobicy,
    Arbeitnow (also onsite: its remote flag says which), Remote OK and Working Nomads."""
    rows, jobicy = [], {}
    for params in [{"industry": i} for i in JOBICY_INDUSTRIES] + [{"tag": t} for t in JOBICY_TAGS]:
        try:
            jobicy.update({j["id"]: j for j in get("https://jobicy.com/api/v2/remote-jobs", count=100, **params).json().get("jobs") or []})
        except requests.HTTPError as e:  # one search refused is one search less
            print(f"WARNING jobicy {params}: {e!r}"[:200])
    rows += feed("jobicy", list(jobicy.values()), "jobTitle", "companyName", "jobGeo", "url", "pubDate", "jobDescription")
    for page in range(1, 6):  # newest first, 100 a page
        data = get("https://www.arbeitnow.com/api/job-board-api", page=page).json()
        for remote in (True, False):
            rows += feed("arbeitnow", [j for j in data.get("data") or [] if bool(j.get("remote")) == remote], "title",
                         "company_name", "location", "url", "created_at", "description", remote)
        if not (data.get("links") or {}).get("next"):
            break
    rows += feed("remoteok", [j for j in get("https://remoteok.com/api").json() if j.get("position")], "position", "company",
                 "location", "url", "epoch", "description")
    rows += feed("workingnomads", get("https://www.workingnomads.com/api/exposed_jobs/").json(), "title", "company_name",
                 "location", "url", "pub_date", "description")
    rows += relocateme()
    ns = {"dc": "http://purl.org/dc/elements/1.1/", "content": "http://purl.org/rss/1.0/modules/content/"}
    for item in ET.fromstring(get("https://jobspresso.co/", feed="job_feed").content).iter("item"):  # its 10 newest
        company, _, place = text(item.findtext("dc:creator", "", ns)).partition("⚲")
        rows += feed("jobspresso", [{"title": item.findtext("title"), "company": company.strip(), "location": place.strip(),
                                     "url": item.findtext("link"), "posted": str(parsedate_to_datetime(item.findtext("pubDate")).date()),
                                     "body": item.findtext("content:encoded", "", ns)}],
                     "title", "company", "location", "url", "posted", "body")
    return rows


def relocateme(pages: int = 5) -> list[dict]:
    """Relocate.me: tech jobs whose employer pays the move (visa and relocation), 20 a page, newest first. A job's address
    names its country, city and company: /netherlands/amsterdam/picnic/software-engineer-10298. transform's
    match_skills marks every one visa_relocation (source 'relocateme')."""
    rows = []
    for page in range(1, pages + 1):
        soup = BeautifulSoup(get("https://relocate.me/international-jobs", page=page).text, "html.parser")
        for a in soup.find_all("a", href=re.compile(r"^/[a-z-]+/[a-z-]+/[a-z0-9-]+/[a-z0-9-]+-\d+$")):
            country, city, company, _ = a["href"].strip("/").split("/")
            # the card reads "Software Engineer in Amsterdam": the title without its city
            title = re.sub(rf"\s+in\s+{re.escape(city.replace('-', ' '))}$", "", " ".join(a.get_text(" ").split()), flags=re.I)
            if country != "remote" and is_tech_role(title):
                rows.append(row("relocateme", None, title, company.replace("-", " ").title(),
                                f"{city.replace('-', ' ').title()}, {country.replace('-', ' ').title()}",
                                f"https://relocate.me{a['href']}", None, None, {"relocation": True}))
    return list({r["job_url"]: r for r in rows}.values())


# Germany's federal employment agency's public job search (its key is published with the API, not a secret). Its results
# have no description, so their skills come from the title. Radarak's searches, shortened.
BA_QUERIES = ["Softwareentwickler", "Data Engineer", "Data Scientist", "Data Analyst", "DevOps", "Cloud Engineer",
              "Frontend Developer", "Backend Developer", "IT-Security", "SAP Berater", "QA Engineer", "UX Designer"]


def bundesagentur() -> list[dict]:
    """Its v6 search (v4, which Radarak reads, answers 403 since 2026-10): the last 7 days' offers per search."""
    rows = []
    for query in BA_QUERIES:
        data = fetch("GET", "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs",
                     headers={"X-API-Key": "jobboerse-jobsuche"},
                     params={"was": query, "angebotsart": 1, "veroeffentlichtseit": 7, "page": 1, "size": 100}).json()
        for j in data.get("ergebnisliste") or []:
            title, ref = j.get("stellenangebotsTitel") or j.get("hauptberuf") or "", j.get("referenznummer")
            spot = ((j.get("stellenlokationen") or [{}])[0].get("adresse") or {})
            country = {"DEUTSCHLAND": "Germany", "OESTERREICH": "Austria", "SCHWEIZ": "Switzerland"}.get(
                spot.get("land") or "DEUTSCHLAND", (spot.get("land") or "").title())
            if ref and is_tech_role(title):
                rows.append(row("bundesagentur", None, title.strip(), j.get("firma"),
                                where(", ".join(filter(None, [spot.get("ort"), country])), j.get("homeofficemoeglich") is True),
                                j.get("externeURL") or f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{ref}",
                                j.get("datumErsteVeroeffentlichung"), None, j))
    return rows


# A board's address in a link or a page, one capture group = its token; Python and Postgres share this syntax. From
# Radarak's dags/jobradar/boards.py (MIT, see LICENSE-THIRD-PARTY).
TOKEN_PATTERNS = {
    "greenhouse": r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)",
    "lever": r"jobs(?:\.eu)?\.lever\.co/([A-Za-z0-9_.-]+)",
    "ashby": r"jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)",
    "smartrecruiters": r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)",
    "workable": r"apply\.workable\.com/(?!j/)([A-Za-z0-9_-]+)",  # /j/<code> is a job, not an account
    "recruitee": r"([A-Za-z0-9-]+)\.recruitee\.com",
    "personio": r"([A-Za-z0-9-]+)\.jobs\.personio\.(?:de|com)",
}
NOT_TOKENS = {"embed", "api", "v1", "jobs", "job", "j", "boards", "careers", "search", "apply", "oneclick-ui", "www", "app",
              "help", "support", "docs", "blog", "widget", "assets", "static", "cdn", ""}


def token(ats: str, raw: str) -> str | None:
    """A board token as the platform names it (lower case, but SmartRecruiters' is case-sensitive), or None for a word
    that is part of the platform's own addresses."""
    raw = raw.strip().strip(".-_")
    clean = raw if ats == "smartrecruiters" else raw.lower()
    return None if clean.lower() in NOT_TOKENS or not 2 <= len(clean) <= 80 else clean


# a Workday career site: tenant, data centre and site ('nvidia.wd5/NVIDIAExternalCareerSite' is its token)
WORKDAY = r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)"


def boards_in(text: str) -> list[tuple[str, str]]:
    """The (platform, token) of every board a page or link names, in order, each once."""
    found = []
    for ats, pattern in TOKEN_PATTERNS.items():
        found += [(ats, t) for raw in re.findall(pattern, text or "") if (t := token(ats, raw))]
    found += [("workday", f"{tenant}.{wd}/{site}") for tenant, wd, site in re.findall(WORKDAY, text or "")
              if site not in ("wday", "job", "jobs")]
    return list(dict.fromkeys(found))


WORKDAY_SEARCHES = ["software", "data", "engineer", "developer"]  # each one's first 100 hits: a big employer has thousands


def posted_on(text: str | None) -> str | None:
    """Workday's 'Posted Today', 'Posted Yesterday', 'Posted 3 Days Ago' as a date; '30+ Days Ago' as 30 days ago."""
    from datetime import date, timedelta
    text = (text or "").lower()
    days = 0 if "today" in text else 1 if "yesterday" in text else int(m.group(1)) if (m := re.search(r"(\d+)", text)) else None
    return str(date.today() - timedelta(days=days)) if days is not None else None


def workday(token: str, company: str) -> list[dict]:
    """A Workday career site's public search (the JSON its own page calls): each of WORKDAY_SEARCHES, 20 a call, its first
    100 hits. A search reads part of a big list, so its source is 'workday', not a '-board' (a job not seen is not closed:
    it lives KEEP_DAYS). Its list has no description: the skills come from the title."""
    site_host, site = token.split("/", 1)
    tenant, host = site_host.split(".")[0], f"https://{site_host}.myworkdayjobs.com"
    jobs = {}
    for search in WORKDAY_SEARCHES:
        for offset in range(0, 100, 20):
            page = fetch("POST", f"{host}/wday/cxs/{tenant}/{site}/jobs", headers={"Accept": "application/json"},
                         json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": search}).json()
            jobs.update({j["externalPath"]: j for j in page.get("jobPostings") or [] if j.get("externalPath")})
            if len(page.get("jobPostings") or []) < 20:
                break
    rows = []
    for path, j in jobs.items():
        where = j.get("locationsText") or ""
        if re.match(r"\d+ Locations", where):  # the first location is in its address: /job/US-CA-Santa-Clara/...
            where = path.split("/")[2].replace("-", " ") if path.count("/") >= 3 else ""
        if is_tech_role(j.get("title")):
            rows.append(row("workday", None, j["title"].strip(), company, where, f"{host}/{site}{path}",
                            posted_on(j.get("postedOn")), None, {**j, "board": f"workday:{token}"}))
    return rows


READERS = {"greenhouse": greenhouse, "workday": workday, "lever": lever, "ashby": ashby, "smartrecruiters": smartrecruiters,
           "workable": workable, "recruitee": recruitee, "personio": personio}
