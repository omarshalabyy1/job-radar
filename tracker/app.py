"""Job tracker: every job the radar found, your status for each, your applications and what the market asks for.
One page per job to be done (tracker/app_pages); the shared data and filters are in tracker/common.py.

Run by docker-compose.yml on http://127.0.0.1:8501, or from the repo root with .env set:
    streamlit run tracker/app.py
"""

import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")
from tracker.common import keep_filters  # noqa: E402

st.set_page_config(page_title="Job tracker", page_icon=":material/radar:", layout="wide")
keep_filters()


def page(name: str, title: str, icon: str, **kwargs) -> st.Page:
    return st.Page(Path(__file__).parent / "app_pages" / f"{name}.py", title=title, icon=f":material/{icon}:", **kwargs)


st.navigation({
    "Your search": [page("today", "Today", "today", default=True), page("jobs", "Jobs", "work"),
                    page("ready", "Ready to apply", "task_alt"), page("applications", "Applications", "mail")],
    "Insights": [page("market", "Market", "monitoring"), page("skills", "Skills", "school"),
                 page("startups", "Startups", "rocket_launch"), page("insights", "Interview rate", "query_stats")],
    "Setup": [page("companies", "Companies", "apartment"), page("cv", "CV", "description"),
              page("contacts", "Contacts", "contacts"), page("runlog", "Last run", "history")],
}).run()
