import re, sys
from pathlib import Path
sys.path.insert(0, "/opt/job-radar")
from job_radar.tailor import pdf_of, pdf_problems

D = Path("/opt/job-radar/output/applications/2026-10-14/raya-riser-ai-engineer")
GH = '<a href="https://github.com/omarshalabyy1/{0}">GitHub</a>'
JOB = ('<div class="block"><div class="role"><div class="what"><b>job-radar: AI job-matching data pipeline</b> — '
       '<span class="grey">Personal project | ' + GH.format("job-radar") + '</span></div><div class="when">2026</div></div><ul>'
       '<li>Collected postings from 10 source groups (APIs, feeds, career pages and email) into PostgreSQL with a daily Airflow pipeline.</li>'
       '<li>Ranked deduplicated postings against the skills in an uploaded CV, with Claude grading the closest matches and a Streamlit tracker for review.</li>'
       '<li>Put every request through one rate-limited client (one request a second per site, holds on 429 and 403), keeping daily runs within each site\'s limits.</li></ul>'
       '<div class="tech"><b>Technologies:</b> Python, Apache Airflow, PostgreSQL, Docker Compose, Claude API, Streamlit</div></div>')
CLIENT = ('<div class="block"><div class="role"><div class="what"><b>client-radar: AI-graded freelance lead pipeline</b> — '
          '<span class="grey">Personal project | ' + GH.format("client-radar") + '</span></div><div class="when">2026</div></div><ul>'
          '<li>Collected new freelance gigs from ten sources (Upwork, Freelancer.com, PeoplePerHour, Mostaql and others) every 30 minutes with Airflow into PostgreSQL.</li>'
          '<li>Scored each gig 0-10 by combining Claude\'s fit grade with code-computed client and chance scores, and emailed only gigs with 15 or fewer proposals, best first.</li></ul>'
          '<div class="tech"><b>Technologies:</b> Python, Apache Airflow, PostgreSQL, Docker Compose, Claude API, Streamlit</div></div>')

html = (D / "cv.html").read_text(encoding="utf-8")
html = re.sub(r'(<h2>Projects</h2>).*?(<h2>Education</h2>)', lambda m: m[1] + "\n" + JOB + "\n" + CLIENT + "\n" + m[2], html, flags=re.S)
(D / "cv.html").write_text(html, encoding="utf-8")
md = (D / "cv.md").read_text(encoding="utf-8")
md = re.sub(r"## Projects\n.*?\n## Education", """## Projects

**job-radar: AI job-matching data pipeline** — Personal project | GitHub | 2026
- Collected postings from 10 source groups (APIs, feeds, career pages and email) into PostgreSQL with a daily Airflow pipeline.
- Ranked deduplicated postings against the skills in an uploaded CV, with Claude grading the closest matches and a Streamlit tracker for review.
- Put every request through one rate-limited client (one request a second per site, holds on 429 and 403), keeping daily runs within each site's limits.
*Technologies: Python, Apache Airflow, PostgreSQL, Docker Compose, Claude API, Streamlit*

**client-radar: AI-graded freelance lead pipeline** — Personal project | GitHub | 2026
- Collected new freelance gigs from ten sources (Upwork, Freelancer.com, PeoplePerHour, Mostaql and others) every 30 minutes with Airflow into PostgreSQL.
- Scored each gig 0-10 by combining Claude's fit grade with code-computed client and chance scores, and emailed only gigs with 15 or fewer proposals, best first.
*Technologies: Python, Apache Airflow, PostgreSQL, Docker Compose, Claude API, Streamlit*

## Education""", md, flags=re.S)
(D / "cv.md").write_text(md, encoding="utf-8")
pdf = pdf_of(html)
(D / "Omar Shalaby Raya AI.pdf").write_bytes(pdf)
print("problems:", pdf_problems(pdf, html))
