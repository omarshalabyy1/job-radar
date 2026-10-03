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
     [r"\bbi\b|business intelligence|power ?bi|tableau|qlik|looker|ssrs|\bmis\b|data visuali[sz]ation|dashboard"
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
# names a place.
PHENOM_SEARCHES = [f"{kw} {where}" for kw in ("data", "AI", "analyst")
                   for where in ("Egypt", "Dubai", "Abu Dhabi", "Riyadh", "Jeddah", "Doha", "Kuwait", "Bahrain",
                                 "Muscat", "United Kingdom", "Germany", "Netherlands", "France", "Spain", "Poland",
                                 "United States")]

# Tanqeeb gathers Wuzzuf, Bayt, Forasna, NaukriGulf, GulfTalent ...: its country sites and the job
# pages its robots.txt allows (no ?keywords searches)
TANQEEB_SITES = {"egypt": "Egypt", "uae": "UAE", "saudi": "Saudi Arabia", "qatar": "Qatar", "kuwait": "Kuwait",
                 "bahrain": "Bahrain", "oman": "Oman"}
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


def is_target(company: str) -> bool:
    return re.search(TARGET_COMPANIES, company, re.I) is not None
