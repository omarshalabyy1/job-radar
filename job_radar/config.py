"""What job_radar looks for: the roles in rank order, the places, the target companies."""

import re

HOURS_OLD = 24  # every run looks back this far: a run after the laptop was asleep or off backfills the last day
# A whole run stays under 300 seconds: each extract step (they run side by side) gets 150 seconds
# and describe 60; what does not fit waits for the next run.
EXTRACT_SECONDS = 150
DESCRIBE_SECONDS = 60

# (rank, label, Indeed/Bayt search term, title patterns that must all match), English and Arabic
ROLES = [
    (1, "AI & Data Engineer", '"AI Data Engineer" OR "Data AI Engineer" OR "ML Data Engineer"',
     [r"\bdata\b", r"\b(ai|ml|genai|llms?|machine learning)\b", r"engineer|developer"]),
    (2, "Data Engineer",
     '"Data Engineer" OR "ETL Developer" OR "Analytics Engineer" OR "Big Data" OR "Data Integration"'
     ' OR "Data Warehouse" OR "Database Developer" OR "Azure Data Engineer" OR "Databricks"',
     [r"data (engineer|engineering|integration|pipeline|platform|warehous|infrastructure|migration|modell?er|developer"
      r"|quality|governance)"
      r"|dataops|\b(etl|elt|dwh)\b|big data|analytics engineer"
      r"|(sql|database|spark|databricks|snowflake|informatica|ssis|talend|hadoop|kafka) (developer|engineer)"
      r"|مهندس بيانات"]),
    (3, "AI Engineer (NLP, LLM, agents, RAG)",
     '"AI Engineer" OR "LLM Engineer" OR "NLP Engineer" OR "Machine Learning Engineer" OR "GenAI Engineer"'
     ' OR "AI Developer" OR "Data Scientist" OR "Generative AI" OR "RAG"',
     [r"\b(ai|ml|nlp|llms?|genai|gen ai|generative ai|machine learning|deep learning|computer vision|rag|agentic"
      r"|conversational ai|chatbot|prompt|langchain|langgraph)\b|data scien|ذكاء اصطناعي|تعلم الآلة",
      r"engineer|developer|specialist|scientist|programmer|researcher|consultant|intern|trainee|مهندس|مطور|أخصائي"]),
    (4, "BI Developer",
     '"BI Developer" OR "Power BI" OR "Business Intelligence" OR "Data Visualization" OR "MIS Specialist"'
     ' OR "Reporting Developer"',
     [r"\bbi\b|business intelligence|power ?bi|tableau|qlik|looker|ssrs|\bmis\b|data visuali[sz]ation"
      r"|dashboards? (developer|designer|specialist|engineer|analyst)"
      r"|report(ing)? (developer|specialist|engineer)|ذكاء الأعمال|باور بي"]),
    (5, "Data Analyst",
     '"Data Analyst" OR "Reporting Analyst" OR "Business Analyst" OR "Analytics Specialist" OR "Product Analyst"',
     [r"(data|business|reporting|insights?|analytics|product|marketing|quantitative) analyst|data analy(sis|tics)"
      r"|analytics (specialist|associate|executive)|data specialist|محلل بيانات"]),
]
# plain keywords for the sources that take no OR syntax (Himalayas, Jooble)
KEYWORDS = ["data engineer", "analytics engineer", "ETL developer", "databricks", "AI engineer",
            "machine learning engineer", "NLP engineer", "LLM engineer", "generative AI", "data scientist",
            "BI developer", "power bi", "data analyst"]
# entry, mid and senior are yours; above senior is not
TOO_SENIOR = r"\b(lead|principal|staff|head|director|manager|vp|chief|architect)\b"
# titles that are never yours, whatever else they say
NOT_RELEVANT = (r"\b(data entry|mlops|llmops|devops|sre|site reliability|technician|teacher|tutor|instructor"
                r"|lecturer|recruiter|accountant|cyber ?security|quality (assurance|control|inspector))\b"
                r"|إدخال بيانات")

# (place, search location, Indeed country)
PLACES = [
    ("Egypt", "Egypt", "egypt"),
    ("UAE", "United Arab Emirates", "united arab emirates"),
    ("Saudi Arabia", "Saudi Arabia", "saudi arabia"),
    ("Qatar", "Qatar", "qatar"),
    ("Kuwait", "Kuwait", "kuwait"),
    ("Bahrain", "Bahrain", "bahrain"),
    ("Oman", "Oman", "oman"),
    ("Europe", "United Kingdom", "uk"),
    ("Europe", "Germany", "germany"),
    ("Europe", "Netherlands", "netherlands"),
    ("Europe", "Ireland", "ireland"),
    ("Europe", "France", "france"),
    ("Europe", "Spain", "spain"),
    ("Europe", "Poland", "poland"),
    ("Europe", "Sweden", "sweden"),
    ("Europe", "Switzerland", "switzerland"),
    ("Europe", "Portugal", "portugal"),
    ("USA", "United States", "usa"),
]
BAYT_PLACES = {"Egypt", "UAE", "Saudi Arabia", "Qatar", "Kuwait", "Bahrain", "Oman"}  # where Bayt has jobs
# a free-text location -> place, for the sources that do not search by place (first match wins)
PLACE_PATTERNS = {
    "Egypt": r"egypt|cairo|giza",
    "UAE": r"\buae\b|united arab emirates|dubai|abu dhabi|sharjah",
    "Saudi Arabia": r"saudi|\bksa\b|riyadh|jeddah|dammam|khobar",
    "Qatar": r"qatar|doha",
    "Kuwait": r"kuwait",
    "Bahrain": r"bahrain|manama",
    "Oman": r"\boman\b|muscat",
    # before Europe: Dublin, OH is in the USA. State codes are upper case, at the end, and not after
    # another code ("Toronto, ON, CA" is Canada); IN and DE are left out (India, Germany).
    "USA": (r"united states|\bu\.s\.a?\b|(?-i:\bUSA?\b)|\b(new york|san francisco|seattle|austin|boston|chicago"
            r"|los angeles|denver|atlanta|dallas|houston|miami|washington|san jose|san diego)\b"
            r"|(?-i:(?<!, [A-Z]{2}), (AL|AK|AZ|AR|CA|CO|CT|FL|GA|HI|ID|IL|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE"
            r"|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)$)"),
    "Europe": (r"\b(united kingdom|uk|england|scotland|wales|london|manchester|germany|berlin|munich|hamburg"
               r"|frankfurt|netherlands|amsterdam|rotterdam|ireland|dublin|france|paris|spain|madrid|barcelona"
               r"|poland|warsaw|krakow|sweden|stockholm|switzerland|zurich|geneva|portugal|lisbon|porto|belgium"
               r"|brussels|austria|vienna|denmark|copenhagen|norway|oslo|finland|helsinki|italy|milan|rome|czechia"
               r"|czech republic|prague|romania|bucharest|greece|athens|europe|european union)\b"),
}
REMOTE_OPEN_TO = r"worldwide|anywhere|global|emea|mena|africa|middle east|egypt"
# Your rule (2026-10-03): an onsite or hybrid job only in Cairo or Giza; anywhere else only remote.
# An Egypt job is dropped only when its location names another Egyptian city (no city: kept).
CAIRO_GIZA = (r"cairo|giza|القاهرة|الجيزة|heliopolis|maadi|nasr city|zamalek|mohandessin|dokki|6th of october"
              r"|october city|sheikh zayed|smart village|fifth settlement|5th settlement")
OTHER_EGYPT = (r"alexandria|\balex\b|الإسكندرية|mansoura|tanta|assiut|asyut|suez|ismailia|port said|damietta"
               r"|hurghada|sharm|sinai|aswan|luxor|zagazig|minya|sohag|beni suef|fayoum|faiyum|sokhna"
               r"|(10th|tenth) of ramadan|sadat city|bahariya|marsa alam|gouna|matrouh|alamein|qena|banha|benha"
               r"|obour|badr city|menoufia|monufia|sharqia|dakahlia|beheira|gharbia|red sea|new valley")
REMOTE = r"remote|work from home|\bwfh\b|عن بعد"
# so the boards (Indeed, Bayt, Workable) search every other place for remote jobs only, and a site
# with no remote filter is searched in these places only
ONSITE_PLACES = {"Egypt"}
# job pages never fetched: LinkedIn (its jobs come only from your alert emails)
NO_FETCH = r"linkedin\.com"
# Your choice (2026-10-03): read public careers pages even when their robots.txt asks crawlers to
# stay away (a few requests a run, no login). Pages behind a bot check are never read either way.
RESPECT_ROBOTS = False

# companies whose jobs are starred and listed first, from any source (with every company in core.company)
TARGET_COMPANIES =(r"vodafone|\b_?vois\b|\borange\b|pwc|pricewaterhouse|deloitte|\bdhl\b|nestl[eé]|\badib\b"
                    r"|abu dhabi islamic|\beg ?bank\b|egyptian gulf bank|etisalat|\be&|advansys|\bnawy\b"
                    r"|alignerr|labelbox|crossover|sumerge|finaira")

# The companies whose own career sites are read live in core.company: add them in the tracker
# (Companies tab) or in sql/schema.sql. A Phenom site searches by keywords only, so each search
# names a place: Egypt, or remote.
PHENOM_SEARCHES = [f"{kw} {where}" for kw in ("data", "AI", "analyst") for where in ("Egypt", "remote")]

# Tanqeeb gathers Wuzzuf, Bayt, Forasna, NaukriGulf, GulfTalent ...: its country sites and the job
# pages its robots.txt allows (no ?keywords searches). It has no remote filter, so Egypt only.
TANQEEB_SITES = {"egypt": "Egypt"}
TANQEEB_PAGES = ["it-jobs", "data-analyst-jobs", "business-analyst-jobs", "python-developer-jobs", "internship-jobs"]


def role_of(title: str) -> int | None:
    """The rank of the first role whose patterns all match the title, seniority aside."""
    t = title.lower()
    if re.search(NOT_RELEVANT, t):
        return None
    for rank, _, _, patterns in ROLES:
        if all(re.search(p, t) for p in patterns):
            return rank
    return None


def too_senior(title: str) -> bool:
    return re.search(TOO_SENIOR, title.lower()) is not None


def place_of(location: str) -> str | None:
    """Egypt, a Gulf country, USA, Europe, Remote (open to Egypt), or None when out of scope. A
    remote job open worldwide is Remote; one tied to a country takes that country."""
    if re.search(r"remote", location, re.I) and re.search(r"worldwide|anywhere|global", location, re.I):
        return "Remote"
    for place, pattern in PLACE_PATTERNS.items():
        if re.search(pattern, location, re.I):
            return place
    if re.search(r"remote", location, re.I) and re.search(REMOTE_OPEN_TO, location, re.I):
        return "Remote"
    return None


def in_reach(place: str, location: str, title: str, remote: bool = False) -> bool:
    """A remote job (not hybrid) in any place; an onsite or hybrid one only in Cairo or Giza.
    remote: the board says so (JobSpy's is_remote), even when the location names only a city."""
    text = f"{location} {title}"
    if remote or place == "Remote" or (re.search(REMOTE, text, re.I) and not re.search(r"hybrid", text, re.I)):
        return True
    if place == "Unknown location":  # kept, unless it says onsite or hybrid (then it is not Cairo or Giza)
        return re.search(r"hybrid|on-?site", text, re.I) is None
    return place == "Egypt" and (re.search(CAIRO_GIZA, location, re.I) is not None
                                 or re.search(OTHER_EGYPT, location, re.I) is None)


def is_target(company: str) -> bool:
    return re.search(TARGET_COMPANIES, company, re.I) is not None
