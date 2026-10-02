-- The job_radar warehouse. Run by `python -m job_radar schema` before every run; safe to re-run.
--
--   raw.job_posting   bronze: every posting each run found, as the source gave it (append-only)
--   core.job          silver: one row per job (same title and company on any board), in scope
--   core.application  your status for a job, set in the tracker
--   core.skill        the skills the demand report counts, as case-insensitive patterns
--   mart.*            gold: views for the tracker and Power BI

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS mart;

CREATE TABLE IF NOT EXISTS raw.job_posting (
    posting_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_date     date        NOT NULL,
    source       text        NOT NULL,  -- linkedin, indeed, bayt, remotive, himalayas, weworkremotely,
                                        -- workable, jooble, or the career page's system (greenhouse, workday ...)
    searched_for text,                  -- the place searched, for the sources that search by place
    title        text,
    company      text,
    location     text,
    job_url      text,
    date_posted  date,
    description  text,
    payload      jsonb       NOT NULL,  -- the whole record as received
    loaded_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS job_posting_run_date ON raw.job_posting (run_date);

CREATE TABLE IF NOT EXISTS core.job (
    job_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_key        text     NOT NULL UNIQUE,  -- lower(title) | lower(company)
    title          text     NOT NULL,
    company        text     NOT NULL,
    location       text     NOT NULL,
    place          text     NOT NULL CHECK (place IN ('Egypt', 'UAE', 'Saudi Arabia', 'Qatar', 'Remote')),
    source         text     NOT NULL,         -- the first source that found it
    job_url        text     NOT NULL,
    role_rank      smallint NOT NULL CHECK (role_rank BETWEEN 1 AND 5),
    role           text     NOT NULL,
    target_company boolean  NOT NULL,
    date_posted    date,
    description    text,
    described_at   timestamptz,               -- its page was read (description found or not)
    fit_score      smallint CHECK (fit_score BETWEEN 0 AND 100),
    fit_reason     text,
    scored_at      timestamptz,
    first_seen     date     NOT NULL,
    last_seen      date     NOT NULL,
    emailed_at     timestamptz
);

CREATE TABLE IF NOT EXISTS core.application (
    job_id     bigint      PRIMARY KEY REFERENCES core.job,
    status     text        NOT NULL CHECK (status IN ('saved', 'applied', 'interview', 'offer', 'rejected', 'ignored')),
    note       text,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS core.skill (
    skill   text PRIMARY KEY,
    pattern text NOT NULL  -- Postgres regular expression, matched case-insensitively (~*)
);
INSERT INTO core.skill (skill, pattern) VALUES
    ('Python', '\mpython\M'), ('SQL', '\msql\M'), ('Spark', '\m(py)?spark\M'), ('Airflow', 'airflow'),
    ('dbt', '\mdbt\M'), ('Kafka', '\mkafka\M'), ('Databricks', 'databricks'), ('Snowflake', 'snowflake'),
    ('BigQuery', 'bigquery'), ('Redshift', 'redshift'), ('Azure Data Factory', 'data factory|\madf\M'),
    ('Microsoft Fabric', 'microsoft fabric'), ('Synapse', 'synapse'), ('SSIS', '\mssis\M'),
    ('ETL / ELT', '\metl\M|\melt\M'), ('Data warehousing', 'data warehous|\mdwh\M'),
    ('Data modeling', 'data model'), ('Streaming', 'streaming|real-time'),
    ('AWS', '\maws\M|amazon web services'), ('Azure', '\mazure\M'), ('GCP', '\mgcp\M|google cloud'),
    ('Docker', 'docker'), ('Kubernetes', 'kubernetes|\mk8s\M'), ('Terraform', 'terraform'),
    ('Git', '\mgit\M|github|gitlab'), ('Linux', 'linux'), ('PostgreSQL', 'postgres'), ('MongoDB', 'mongo'),
    ('Power BI', 'power ?bi'), ('DAX', '\mdax\M'), ('Excel', '\mexcel\M'), ('Tableau', 'tableau'),
    ('Looker', 'looker'), ('Statistics', 'statistic'), ('Pandas', 'pandas'),
    ('Machine learning', 'machine learning|\mml\M'), ('Deep learning', 'deep learning'),
    ('PyTorch', 'pytorch'), ('TensorFlow', 'tensorflow'), ('scikit-learn', 'scikit|sklearn'),
    ('NLP', '\mnlp\M|natural language'), ('LLMs', '\mllms?\M|large language model'),
    ('RAG', '\mrag\M|retrieval[- ]augmented'), ('AI agents', 'agentic|\mai agents?\M|multi-agent'),
    ('LangChain / LangGraph', 'langchain|langgraph'), ('LlamaIndex', 'llama ?index'),
    ('Hugging Face', 'hugging ?face'), ('OpenAI API', 'openai|\mgpt'), ('MLOps', 'mlops|mlflow|kubeflow'),
    ('Vector databases', 'vector (db|database|store|search)|pinecone|weaviate|qdrant|chroma|pgvector|faiss|milvus'),
    ('FastAPI', 'fastapi')
ON CONFLICT (skill) DO UPDATE SET pattern = EXCLUDED.pattern;

-- How many of the last 90 days' described jobs of each role ask for each skill.
CREATE OR REPLACE VIEW mart.skill_demand AS
WITH described AS (
    SELECT role_rank, role, description FROM core.job
    WHERE description IS NOT NULL AND first_seen >= current_date - 90
)
SELECT d.role_rank, d.role, s.skill, count(*) AS jobs,
       round(100.0 * count(*) / (SELECT count(*) FROM described x WHERE x.role_rank = d.role_rank), 1) AS pct_of_role
FROM described d
JOIN core.skill s ON d.description ~* s.pattern
GROUP BY d.role_rank, d.role, s.skill;

-- New jobs a day, by place, role and source.
CREATE OR REPLACE VIEW mart.jobs_daily AS
SELECT first_seen, place, role_rank, role, source, count(*) AS jobs, round(avg(fit_score)) AS avg_fit_score
FROM core.job
GROUP BY first_seen, place, role_rank, role, source;

-- Every job with your status ('new' when you have not set one).
CREATE OR REPLACE VIEW mart.job_status AS
SELECT j.job_id, j.role_rank, j.role, j.title, j.company, j.place, j.location, j.source, j.job_url,
       j.target_company, j.fit_score, j.fit_reason, j.date_posted, j.first_seen,
       coalesce(a.status, 'new') AS status, a.note, a.updated_at
FROM core.job j
LEFT JOIN core.application a USING (job_id);
