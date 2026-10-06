-- The job_radar warehouse. Run by `python -m job_radar schema` before every run; safe to re-run.
--
--   raw.job_posting   bronze: each posting once (source + link), as the source gave it the first time
--   core.job          silver: one row per job (same title and company on any board), in scope
--   core.application  your status for a job, set in the tracker
--   core.skill        your skills: the Data Engineering and Generative AI courses, as patterns
--   core.job_skill    which of your skills each job asks for, stored once by match_skills
--   mart.*            gold: views for the email and the tracker

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS mart;

CREATE TABLE IF NOT EXISTS raw.job_posting (
    posting_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_date     date        NOT NULL,
    source       text        NOT NULL,  -- indeed, bayt, himalayas, weworkremotely, tanqeeb, workable, jooble,
                                        -- email, a company (orange, dhl ...), or the career page's system
                                        -- (greenhouse, workday ...)
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
-- a posting found again (next run, another search) is not stored twice
CREATE UNIQUE INDEX IF NOT EXISTS job_posting_source_url ON raw.job_posting (source, job_url);

CREATE TABLE IF NOT EXISTS core.job (
    job_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_key        text     NOT NULL UNIQUE,  -- normalized title | company (steps.job_key): one row per job
    title          text     NOT NULL,
    company        text     NOT NULL,
    location       text     NOT NULL,
    place          text     NOT NULL,         -- a place in settings.yaml, Remote or Unknown location
    source         text     NOT NULL,         -- the first source that found it (a remote job: its remote listing)
    job_url        text     NOT NULL,
    role_rank      smallint NOT NULL,         -- the role's position in settings.yaml (1 = first)
    role           text     NOT NULL,
    target_company boolean  NOT NULL,
    date_posted    date,
    description    text,
    described_at   timestamptz,               -- its page was read (description found or not)
    first_seen     date     NOT NULL,
    emailed_at     timestamptz                -- set once: a job is never emailed twice
);
CREATE INDEX IF NOT EXISTS job_first_seen ON core.job (first_seen);
-- when the posting went up (steps.posted_at: the source's time, else its date, else when the radar
-- captured it); a warehouse made before the column existed gets it here
ALTER TABLE core.job ADD COLUMN IF NOT EXISTS posted_at timestamptz;
-- A job first seen over 4 days ago (config.KEEP_DAYS) is deleted by transform with its raw postings,
-- unless you noted or applied to it; only its key stays here, so a job still posted later is never
-- stored or emailed again.
CREATE TABLE IF NOT EXISTS core.job_seen (
    job_key text PRIMARY KEY
);

-- Your companies: settings.yaml (companies) is the list, made the same here by the schema step every
-- run. A starred one's jobs are starred from any source, and a careers page, when given, is read at
-- most once a day, whatever its robots.txt says. The platform is detected on the next run; 'blocked'
-- (turns scripts away) and 'unreachable' (page not found) sites are retried daily, as are 'forbidden'
-- rows left from before robots.txt was ignored.
CREATE TABLE IF NOT EXISTS core.company (
    company     text PRIMARY KEY,
    careers_url text,         -- its careers page or portal; empty = starred only
    platform    text,         -- workable, greenhouse, lever, ashby, phenom, successfactors, rss, page,
                              -- or forbidden, blocked, unreachable, covered (another source reads it)
    api         text,         -- what the platform's reader calls (an account, a feed, the page)
    note        text,         -- why a site cannot be read
    checked_at  timestamptz,
    added_at    timestamptz NOT NULL DEFAULT now(),
    starred     boolean NOT NULL DEFAULT true  -- false: its site is read, its jobs are not starred
);
-- a warehouse made before the column existed gets it (CREATE TABLE IF NOT EXISTS leaves it as it is)
ALTER TABLE core.company ADD COLUMN IF NOT EXISTS starred boolean NOT NULL DEFAULT true;

-- Your CVs, uploaded in the tracker (CV tab): their words are matched against each job's skills,
-- and the newest is exported to career-ops.
CREATE TABLE IF NOT EXISTS core.cv (
    label       text PRIMARY KEY,           -- e.g. 'AI & Data Engineer'
    filename    text NOT NULL,
    text        text NOT NULL,
    uploaded_at timestamptz NOT NULL DEFAULT now()
);
-- Which of the skills each CV shows, stored by match_skills.
CREATE TABLE IF NOT EXISTS core.cv_skill (
    label text NOT NULL REFERENCES core.cv ON DELETE CASCADE,
    skill text NOT NULL,
    PRIMARY KEY (label, skill)
);

CREATE TABLE IF NOT EXISTS core.application (
    job_id     bigint      PRIMARY KEY REFERENCES core.job,
    status     text        NOT NULL CHECK (status IN ('new', 'saved', 'applied', 'interview', 'offer', 'rejected', 'ignored')),
    note       text,
    updated_at timestamptz NOT NULL DEFAULT now()
);
-- 'new' is stored when a job still marked new has a note; warehouses made before that get the wider check here.
ALTER TABLE core.application DROP CONSTRAINT IF EXISTS application_status_check;
ALTER TABLE core.application ADD CONSTRAINT application_status_check
    CHECK (status IN ('new', 'saved', 'applied', 'interview', 'offer', 'rejected', 'ignored'));

-- Your skills (Data Engineering and Generative AI). Reloaded on every run, so this list is the
-- only place to change them.
CREATE TABLE IF NOT EXISTS core.skill (
    skill   text PRIMARY KEY,
    track   text NOT NULL CHECK (track IN ('Data Engineering', 'Generative AI')),
    pattern text NOT NULL  -- Postgres regular expression, matched case-insensitively (~*)
);
DELETE FROM core.skill;
INSERT INTO core.skill (track, skill, pattern) VALUES
    ('Data Engineering', 'SQL', '\msql\M'),
    ('Data Engineering', 'SQL Server', 'sql server|mssql|t-sql|tsql'),
    ('Data Engineering', 'Data modeling', 'data model|\merd\M|entity relationship|normali[sz]ation'),
    ('Data Engineering', 'Data warehousing', 'data warehous|\mdwh\M|\molap\M'),
    ('Data Engineering', 'Star schema / SCD', 'star[- ]schema|snowflake schema|dimensional model|fact table|\mscd\M|slowly changing'),
    ('Data Engineering', 'ETL / ELT', '\metl\M|\melt\M'),
    ('Data Engineering', 'Data lake / lakehouse', 'data lake|lakehouse|delta lake|medallion'),
    ('Data Engineering', 'Query optimization', 'query optimi|query tuning|performance tuning|execution plan|indexing'),
    ('Data Engineering', 'dbt', '\mdbt\M'),
    ('Data Engineering', 'Data quality', 'data quality|data validation|great expectations'),
    ('Data Engineering', 'Data governance', 'data governance|data lineage|data catalog|metadata management'),
    ('Data Engineering', 'NoSQL', 'nosql|mongo|cassandra|dynamodb'),
    ('Data Engineering', 'Python', '\mpython\M'),
    ('Data Engineering', 'Pandas / NumPy', 'pandas|numpy'),
    ('Data Engineering', 'REST APIs', 'rest(ful)? apis?|api integration|postman'),
    ('Data Engineering', 'Azure', '\mazure\M'),
    ('Data Engineering', 'Azure Data Factory', 'data factory|\madf\M'),
    ('Data Engineering', 'Databricks', 'databricks'),
    ('Data Engineering', 'Azure Data Lake / Blob', '\madls|data lake storage|blob storage'),
    ('Data Engineering', 'Spark', '\m(py)?spark\M'),
    ('Data Engineering', 'Hadoop / HDFS', 'hadoop|\mhdfs\M|mapreduce|\mhive\M'),
    ('Data Engineering', 'Kafka / streaming', 'kafka|stream processing|real-time data|event hub'),
    ('Data Engineering', 'Airflow', 'airflow'),
    ('Data Engineering', 'SSIS', '\mssis\M'),
    ('Data Engineering', 'Informatica', 'informatica'),
    ('Data Engineering', 'Linux / shell', 'linux|shell script|\mbash\M'),
    ('Data Engineering', 'Docker', 'docker'),
    ('Data Engineering', 'Power BI', 'power ?bi|\mdax\M|power query'),
    ('Data Engineering', 'Excel', '\mexcel\M'),
    ('Data Engineering', 'Git', '\mgit\M|github|gitlab'),
    ('Data Engineering', 'AWS', '\maws\M|amazon web services|redshift'),
    ('Data Engineering', 'GCP / BigQuery', '\mgcp\M|google cloud|bigquery'),
    ('Generative AI', 'LLMs', '\mllms?\M|large language model'),
    ('Generative AI', 'Generative AI', 'generative ai|\mgen ?ai\M'),
    ('Generative AI', 'Prompt engineering', 'prompt engineering|prompt design|few-shot'),
    ('Generative AI', 'RAG', '\mrag\M|retrieval[- ]augmented'),
    ('Generative AI', 'Embeddings / semantic search', 'embedding|semantic search|vector search|similarity search|hybrid search'),
    ('Generative AI', 'Vector databases', 'vector (db|database|store)|qdrant|pinecone|weaviate|pgvector|faiss|milvus|chroma'),
    ('Generative AI', 'LangChain / LangGraph', 'langchain|langgraph'),
    ('Generative AI', 'AI agents', 'agentic|\mai agents?\M|multi-agent|autonomous agent'),
    ('Generative AI', 'Tool / function calling', 'function calling|tool calling'),
    ('Generative AI', 'MCP', 'model context protocol|\mmcp\M'),
    ('Generative AI', 'OpenAI / Gemini APIs', 'openai|\mgpt|gemini|anthropic|claude'),
    ('Generative AI', 'Hugging Face / Transformers', 'hugging ?face|transformer'),
    ('Generative AI', 'Fine-tuning (LoRA, PEFT)', 'fine-?tun|\mlora\M|qlora|\mpeft\M'),
    ('Generative AI', 'LLM evaluation', 'ragas|deepeval|llm evaluation|hallucination'),
    ('Generative AI', 'LLMOps / MLflow', 'llmops|mlops|mlflow|langsmith|langfuse'),
    ('Generative AI', 'Model serving (Ollama, vLLM)', 'ollama|vllm|model serving|inference'),
    ('Generative AI', 'NLP', '\mnlp\M|natural language|named entity|\mner\M'),
    ('Generative AI', 'OCR / document AI', '\mocr\M|document (ai|processing|parsing)|docling'),
    ('Generative AI', 'Multimodal / vision', 'multimodal|vision-language|\mvlms?\M|computer vision|speech-to-text|text-to-speech'),
    ('Generative AI', 'FastAPI', 'fastapi'),
    ('Generative AI', 'Pydantic', 'pydantic'),
    ('Generative AI', 'Async Python', 'asyncio|aiohttp'),
    ('Generative AI', 'Deep learning (PyTorch, TensorFlow)', 'pytorch|tensorflow|deep learning|neural network'),
    ('Generative AI', 'Machine learning', 'machine learning|\mml\M|scikit|sklearn'),
    ('Generative AI', 'Diffusion / image generation', 'stable diffusion|diffusion model|dall-?e|image generation'),
    ('Generative AI', 'GenAI security', 'prompt injection|guardrail|jailbreak');

-- Matched once per job by the match_skills step (a regex scan of every job on every read would
-- slow down as the warehouse grows); the skill is kept as text so the list above can change.
CREATE TABLE IF NOT EXISTS core.job_skill (
    job_id bigint NOT NULL REFERENCES core.job ON DELETE CASCADE,
    skill  text   NOT NULL,
    PRIMARY KEY (job_id, skill)
);

-- Views hold no data: dropped and created again on every run, so a change here always applies.
DROP VIEW IF EXISTS mart.job_status, mart.skill_demand, mart.jobs_daily, mart.job_skill;

-- Every job with how many of your skills it asks for, how much of that your best CV shows, and
-- your status ('new' until you set one).
CREATE VIEW mart.job_status AS
SELECT j.job_id, j.role_rank, j.role, j.title, j.company, j.place, j.location, j.source, j.job_url,
       j.target_company, coalesce(k.skill_matches, 0) AS skill_matches, k.skills_matched,
       j.description IS NOT NULL AS described, j.date_posted, j.first_seen, j.emailed_at,
       coalesce(a.status, 'new') AS status, a.note, a.updated_at, v.cv_label, v.cv_coverage, j.posted_at,
       -- apply within 48 hours of the posting: 1 within 12 hours, 2 within 24, 3 within 48, 4 older
       CASE WHEN now() - j.posted_at <= interval '12 hours' THEN 1 WHEN now() - j.posted_at <= interval '24 hours' THEN 2
            WHEN now() - j.posted_at <= interval '48 hours' THEN 3 ELSE 4 END AS fresh_level
FROM core.job j
LEFT JOIN (SELECT job_id, count(*) AS skill_matches, string_agg(skill, ', ' ORDER BY skill) AS skills_matched
           FROM core.job_skill GROUP BY job_id) k USING (job_id)
LEFT JOIN (SELECT DISTINCT ON (job_id) job_id, label AS cv_label, cv_coverage
           FROM (SELECT k.job_id, c.label, round(100.0 * count(cs.skill) / count(*)) AS cv_coverage
                 FROM core.job_skill k CROSS JOIN core.cv c
                 LEFT JOIN core.cv_skill cs ON cs.label = c.label AND cs.skill = k.skill
                 GROUP BY k.job_id, c.label) per_cv
           ORDER BY job_id, cv_coverage DESC) v USING (job_id)
LEFT JOIN core.application a USING (job_id);

-- How many of the last 90 days' described jobs of each role ask for each of your skills.
CREATE VIEW mart.skill_demand AS
WITH described AS (
    SELECT job_id, role_rank, role FROM core.job
    WHERE description IS NOT NULL AND first_seen >= current_date - 90
)
SELECT d.role_rank, d.role, s.track, k.skill, count(*) AS jobs,
       round(100.0 * count(*) / (SELECT count(*) FROM described x WHERE x.role_rank = d.role_rank), 1) AS pct_of_role
FROM described d
JOIN core.job_skill k USING (job_id)
JOIN core.skill s USING (skill)
GROUP BY d.role_rank, d.role, s.track, k.skill;

-- New jobs a day, by place, role and source.
CREATE VIEW mart.jobs_daily AS
SELECT first_seen, place, role_rank, role, source, count(*) AS jobs
FROM core.job
GROUP BY first_seen, place, role_rank, role, source;
