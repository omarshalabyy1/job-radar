"""What job_radar looks for: the roles in rank order, the places, the target companies."""

import re

HOURS_OLD = 72  # every source looks back this far; a day the laptop slept through is caught up
CANDIDATE = "based in New Cairo, Egypt; open to Egypt, the Gulf (UAE, Saudi Arabia, Qatar) and remote"

# (rank, label, LinkedIn/Indeed/Bayt search term, title patterns that must all match)
ROLES = [
    (1, "AI & Data Engineer", '"AI Data Engineer" OR "Data AI Engineer"',
     [r"\bdata\b", r"\b(ai|ml|genai|llm|machine learning)\b", r"engineer|developer"]),
    (2, "Data Engineer", '"Data Engineer" OR "ETL Developer" OR "Analytics Engineer"',
     [r"data engineer|\betl\b|big data|data platform|analytics engineer|data warehouse|\bdwh\b"]),
    (3, "AI Engineer (NLP, LLM, agents, RAG)",
     '"AI Engineer" OR "LLM Engineer" OR "NLP Engineer" OR "Machine Learning Engineer" OR "GenAI Engineer"',
     [r"\b(ai|ml|nlp|llms?|genai|gen ai|generative ai|machine learning|deep learning|rag|agentic)\b",
      r"engineer|developer"]),
    (4, "BI Developer", '"BI Developer" OR "Power BI" OR "Business Intelligence"',
     [r"\bbi\b|business intelligence|power ?bi|tableau"]),
    (5, "Data Analyst", '"Data Analyst" OR "Reporting Analyst"',
     [r"(data|business|reporting|insights?) analyst|data analytics"]),
]
# plain keywords for the sources that take no OR syntax (Himalayas, Jooble)
KEYWORDS = ["data engineer", "AI engineer", "machine learning engineer", "BI developer", "power bi",
            "data analyst"]
TOO_SENIOR = r"\b(senior|sr|lead|principal|staff|head|director|manager|vp|chief|architect)\b"

# (place, LinkedIn location, Indeed country)
PLACES = [
    ("Egypt", "Egypt", "egypt"),
    ("UAE", "United Arab Emirates", "united arab emirates"),
    ("Saudi Arabia", "Saudi Arabia", "saudi arabia"),
    ("Qatar", "Qatar", "qatar"),
]
# a free-text location -> place, for the sources that do not search by place
PLACE_PATTERNS = {
    "Egypt": r"egypt|cairo|giza",
    "UAE": r"\buae\b|united arab emirates|dubai|abu dhabi|sharjah",
    "Saudi Arabia": r"saudi|\bksa\b|riyadh|jeddah|dammam|khobar",
    "Qatar": r"qatar|doha",
}
REMOTE_OPEN_TO = r"worldwide|anywhere|global|emea|mena|africa|middle east|egypt"
# job pages never fetched: LinkedIn (its jobs come only from your alert emails) and Wuzzuf (blocks scripts)
NO_FETCH = r"linkedin\.com|wuzzuf\.net"

# companies whose jobs are starred and listed first, from any source
TARGET_COMPANIES = (r"vodafone|\b_?vois\b|\borange\b|pwc|pricewaterhouse|deloitte|\bdhl\b|nestl[eé]|\badib\b"
                    r"|abu dhabi islamic|\beg ?bank\b|egyptian gulf bank|etisalat|\be&|advansys|\bnawy\b"
                    r"|alignerr|labelbox|crossover")

# companies on Workable, read through its public widget API (the slug in apply.workable.com/<slug>)
WORKABLE_ACCOUNTS = ["nawy-real-estate"]


def role_of(title: str) -> int | None:
    """The rank of the first role whose patterns all match the title, seniority aside."""
    t = title.lower()
    for rank, _, _, patterns in ROLES:
        if all(re.search(p, t) for p in patterns):
            return rank
    return None


def too_senior(title: str) -> bool:
    return re.search(TOO_SENIOR, title.lower()) is not None


def place_of(location: str) -> str | None:
    """Egypt, UAE, Saudi Arabia, Qatar, Remote (open to Egypt), or None when out of scope."""
    for place, pattern in PLACE_PATTERNS.items():
        if re.search(pattern, location, re.I):
            return place
    if re.search(r"remote", location, re.I) and re.search(REMOTE_OPEN_TO, location, re.I):
        return "Remote"
    return None


def is_target(company: str) -> bool:
    return re.search(TARGET_COMPANIES, company, re.I) is not None
