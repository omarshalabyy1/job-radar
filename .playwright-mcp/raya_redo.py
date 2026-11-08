import sys
from pathlib import Path
sys.path.insert(0, "/opt/job-radar")
from job_radar.tailor import Draft, Entry, Education, Link, SkillGroup, write_draft

D = Path("/opt/job-radar/output/applications/2026-10-14/raya-riser-ai-engineer")
for old in D.glob("*.pdf"):
    old.unlink()

letter = """Dear Hiring Team,

Thank you for the opportunity to apply for the Riser AI Engineer position at AMAN Holding. I would be grateful to be considered.

At Null Labs, I am responsible for the data platform behind Argus, a Walmart deduction-recovery product, working independently in a small remote team. I automated a daily feed from Walmart's supplier API into a PostgreSQL warehouse, reconciled every payment to its invoice to the cent, and built a rules knowledge base that grounds the team's LLM agents and organises their disputes by deadline.

Previously, at Bosch Car Service, I worked with the finance and procurement teams to reduce the monthly reporting cycle from three days to under two hours, with every figure reconciled to source.

I would welcome the opportunity to discuss how I could support your squad. Thank you for your time and consideration.

Yours sincerely,
Omar Shalaby"""

d = Draft(
    name="Omar Shalaby",
    title_line="AI Engineer | Data Engineering, RAG Pipelines & LLM Applications",
    contact=[Link(text="Rehab, Cairo, Egypt", url=""), Link(text="", url=""),
             Link(text="omarshalaby.data@gmail.com", url="mailto:omarshalaby.data@gmail.com"),
             Link(text="LinkedIn", url="https://www.linkedin.com/in/omarshalaby1"),
             Link(text="github.com/omarshalabyy1", url="https://github.com/omarshalabyy1"),
             Link(text="Portfolio: omarlabs.dev", url="https://omarlabs.dev")],
    summary=("AI Engineer with a strong data engineering foundation, building reliable data pipelines, warehouses and "
             "the LLM applications, RAG pipelines and AI agents that run on them, using Python, SQL, LangChain, LangGraph, "
             "Airflow and Docker. Gathers requirements from business stakeholders, translates them into technical "
             "solutions and owns delivery end to end, working independently in a small remote team. Known for "
             "ownership, accountability and data quality checks with an audit trail on every run."),
    skills=[SkillGroup(label="Generative AI & LLM Application Development", items="LLM applications, RAG pipelines, agentic workflows, AI agent development, LangChain, LangGraph, LLM APIs (OpenAI, Gemini)"),
            SkillGroup(label="AI Integration & Workflow Orchestration", items="API integration, REST APIs, FastAPI, tool and function calling, Apache Airflow, integration with internal systems and data sources"),
            SkillGroup(label="Prompt Engineering & Retrieval Optimization", items="prompt engineering (few-shot, structured output), retrieval strategies, hybrid search, reranking, pgvector, evaluation metrics (RAGAS)"),
            SkillGroup(label="AI Deployment & Monitoring", items="Docker, Docker Compose, Git/GitHub, production monitoring and tracing (LangSmith, Langfuse), run audit trails"),
            SkillGroup(label="Data Engineering & SQL", items="ETL/ELT, incremental and idempotent loads, data validation, dimensional modeling, star schemas, PostgreSQL, SQL Server, dbt, Python (Pandas), Power BI")],
    experience=[
        Entry(title="AI & Data Engineer", org="Null Labs | Florida, United States (Remote)", when="Apr 2026 – Present (6 mos)", technologies="", repo_url="", bullets=[
            "Own the data platform behind Argus, a Walmart deduction-recovery product, end to end, working independently in a three-person remote team.",
            "Replaced manual downloads with a hands-off daily feed from Walmart's supplier REST API and portal into a six-layer PostgreSQL warehouse.",
            "Matched every short payment to its invoice to the cent, so each deduction traces back to its source with no manual digging.",
            "Gave the recovery team one working list, sorted by filing deadline, through a versioned rules knowledge base that classifies each deduction by code and root cause.",
            "Built schema-validated, idempotent loads that quarantine bad rows and keep an audit trail, orchestrated in Airflow on Docker Compose.",
            "Grounded LLM agents in the versioned knowledge base and integrated them with internal data sources, so each AI answer traces back to source facts."]),
        Entry(title="BI Developer", org="Bosch Car Service | Cairo, Egypt (Hybrid)", when="Apr 2025 – Mar 2026 (1 yr)", technologies="", repo_url="", bullets=[
            "Cut the monthly reporting cycle from 3 days to under 2 hours by automating Excel consolidation with Power Query and Python.",
            "Made every reported figure match the source finance reports by building reconciliation checks."]),
        Entry(title="Operations Engineer", org="Bosch Car Service | Cairo, Egypt (On-site)", when="Oct 2024 – Mar 2025 (6 mos)", technologies="", repo_url="", bullets=[
            "Gave management clear visibility of spending by tracking inventory, purchasing, supplier performance and cost KPIs in Excel."])],
    projects=[
        Entry(title="job-radar: Job-market data pipeline", org="Personal project", when="2026", repo_url="https://github.com/omarshalabyy1/job-radar",
              technologies="Python, Apache Airflow, PostgreSQL, Docker Compose, Streamlit", bullets=[
            "Built a daily Airflow pipeline that ingests postings from 10 source groups (APIs, feeds, career pages and email) into raw and core PostgreSQL layers.",
            "Routed every request through one rate-limited client (one request a second per site, holds on 429 and 403), keeping daily runs within each site's limits."]),
        Entry(title="client-radar: Freelance market data pipeline", org="Personal project", when="2026", repo_url="https://github.com/omarshalabyy1/client-radar",
              technologies="Python, Apache Airflow, PostgreSQL, Docker Compose, Streamlit", bullets=[
            "Ingested new gigs from ten sources into PostgreSQL every 30 minutes with Airflow, each source on its own schedule and inside its limits.",
            "Loaded each gig once with idempotent loads and scored it from platform facts, feeding a Streamlit tracker and a deduplicated email."])],
    education=[Education(degree="B.Sc. Mechatronics Engineering", school="Arab Academy for Science and Technology (AAST)", when="2019 – 2024",
                         note="Graduation project: cloud-connected remotely operated vehicle (Python, AWS), ranked #3 in the SWIFT ACT competition.")],
    certifications=[],
    languages="Arabic (native), English (fluent)",
    file_tag="AI",
    cover_letter_md=letter,
    notes_md=(D / "notes.md").read_text(encoding="utf-8"),
)
print("problems:", write_draft(D, d, "Raya"))
print(sorted(p.name for p in D.iterdir()))
