-- The job_radar warehouse. Run by `python -m job_radar schema` before every run; safe to re-run.
--
--   raw.job_posting   bronze: each posting once (source + link), as the source gave it the first time
--   core.job          silver: one row per job (same title and company on any board), in scope
--   core.application  your status for a job, set in the tracker
--   core.skill        170 tech skills as patterns; yours carry a track (Data Engineering, BI & Analytics, Generative AI)
--   core.geo_pattern  locations to countries and regions; raw.fx_rate  the ECB's daily euro rates
--   core.job_skill    which of your skills each job asks for, stored once by match_skills
--   core.step_run     the run log: one row per step per run (runlog.py)
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
-- the last day the source listed it (load: a posting found again only moves this); transform reads the postings seen in
-- its window, and the ones not seen for config.WORK_DAYS are deleted
ALTER TABLE raw.job_posting ADD COLUMN IF NOT EXISTS last_seen date;
UPDATE raw.job_posting SET last_seen = run_date WHERE last_seen IS NULL;
CREATE INDEX IF NOT EXISTS job_posting_last_seen ON raw.job_posting (last_seen);

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
-- the company's size in employees ('51-200'), from any posting of that company that gives it
-- (Indeed, freehire); empty when no source does
ALTER TABLE core.job ADD COLUMN IF NOT EXISTS company_size text;
-- what bars someone living in Egypt, set by the eligibility step: its board's "who can apply" field ("open to United
-- States only") or Claude Haiku's reason ("US citizens only"); empty when nothing does, or not judged yet
ALTER TABLE core.job ADD COLUMN IF NOT EXISTS restriction text;
-- when the eligibility step judged it (once per job); empty until then
ALTER TABLE core.job ADD COLUMN IF NOT EXISTS eligibility_at timestamptz;
-- the employer's or original board's own page for the job, when its source gives one (steps.apply_url); the
-- tracker shows it, else the careers page of a company you track, else job_url
ALTER TABLE core.job ADD COLUMN IF NOT EXISTS apply_url text;
-- the years of experience the posting asks for (its lowest bound, the most of several): steps.years_required reads the
-- description at match_skills, else grade_claude's Claude; empty when neither found one
ALTER TABLE core.job ADD COLUMN IF NOT EXISTS years_required smallint;
-- how close its text is to your nearest CV (embed.py, 0-1, cosine); empty until embed has seen it
ALTER TABLE core.job ADD COLUMN IF NOT EXISTS cv_similarity real;
-- the one line of your CVs the job is closest to (embed.py): why it matched
ALTER TABLE core.job ADD COLUMN IF NOT EXISTS cv_evidence text;
-- Where and how it can be done, set by match_skills from its location and place (core.geo_pattern): Remote, Hybrid or
-- Onsite; the countries and regions it names (a remote job: who may apply; an onsite one: the office). A remote job open
-- anywhere has region Worldwide and no country. Empty arrays when nothing matched.
ALTER TABLE core.job ADD COLUMN IF NOT EXISTS work_mode text;
-- in_scope: one of your roles, not above senior, in reach from Cairo and not barred (transform, the rules in settings.yaml).
-- False: another tech job, kept for the website only (features.market); every step that spends Claude, Ollama, a
-- page read or your attention (eligibility, embed, grade, describe, the emails, the tracker, mart.*) reads in_scope jobs only.
-- A job already stored before the column existed is yours (true).
ALTER TABLE core.job ADD COLUMN IF NOT EXISTS in_scope boolean NOT NULL DEFAULT true;
-- the last day a source listed it (transform): a company board's job it no longer lists is closed (config.CLOSED_AFTER_DAYS)
ALTER TABLE core.job ADD COLUMN IF NOT EXISTS last_seen date;
-- what kind of tech job it is, for every job (match_skills: core.role_family, core.role_track, core.seniority)
ALTER TABLE core.job ADD COLUMN IF NOT EXISTS role_family text;
ALTER TABLE core.job ADD COLUMN IF NOT EXISTS track text;
ALTER TABLE core.job ADD COLUMN IF NOT EXISTS seniority text;
ALTER TABLE core.job ADD COLUMN IF NOT EXISTS countries text[];
ALTER TABLE core.job ADD COLUMN IF NOT EXISTS regions text[];
-- What its title and description say about applying from abroad (match_skills): visa sponsorship or relocation offered
-- (and not refused), nationals only (Saudization, "UAE nationals only" ...), Arabic required
ALTER TABLE core.job ADD COLUMN IF NOT EXISTS visa_relocation boolean;
ALTER TABLE core.job ADD COLUMN IF NOT EXISTS nationals_only boolean;
ALTER TABLE core.job ADD COLUMN IF NOT EXISTS arabic_required boolean;
-- The pay its board gives, as given (transform: Indeed and Bayt through JobSpy, Himalayas, Jobicy): lowest and highest,
-- currency code, period (yearly, monthly, hourly ...); then a year's pay in US dollars and euros at the ECB rate of the
-- day it was first seen (fx.py, run by match_skills); salary_plausible is false outside 5,000-1,500,000 USD a year.
ALTER TABLE core.job ADD COLUMN IF NOT EXISTS salary_min numeric;
ALTER TABLE core.job ADD COLUMN IF NOT EXISTS salary_max numeric;
ALTER TABLE core.job ADD COLUMN IF NOT EXISTS salary_currency text;
ALTER TABLE core.job ADD COLUMN IF NOT EXISTS salary_period text;
ALTER TABLE core.job ADD COLUMN IF NOT EXISTS salary_min_usd integer;
ALTER TABLE core.job ADD COLUMN IF NOT EXISTS salary_max_usd integer;
ALTER TABLE core.job ADD COLUMN IF NOT EXISTS salary_min_eur integer;
ALTER TABLE core.job ADD COLUMN IF NOT EXISTS salary_max_eur integer;
ALTER TABLE core.job ADD COLUMN IF NOT EXISTS salary_plausible boolean;

-- What kind of tech job a title is, for the website's role and track filters (match_skills sets core.job.role_family,
-- track and seniority with these). Copied from Radarak's int_postings_classified and role_tracks (MIT, Copyright (c) 2026
-- Moamen Moussa; see LICENSE-THIRD-PARTY): the first branch that matches wins.
CREATE OR REPLACE FUNCTION core.role_family(title text) RETURNS text LANGUAGE sql IMMUTABLE AS $role$
SELECT
    case
        -- ---------------------------------------------------------------- not tech
        when title ~* '(\mwarehouse|\mlager)\s*(associate|lead|operations?|operative|worker|clerk|picker|supervisor|manager|shift|team|employee|mitarbeiter)|schichtleiter|(operations|logistics)\s+manager,?\s+warehouse'
         and title !~* 'data|\mdwh\M|\medw\M|\mbi\M|analytics'
            then 'Not a tech role'
        -- business jobs that only mention AI (same rule as is_tech_role in filters.py)
        when title ~* '\mai\M|artificial\s+intelligence'
         and title ~* 'trainer|strateg|transformation|\mgtm\M|go[\s-]to[\s-]market|market(er|ing)|growth|talent|accelerator|operations|communications|business\s+risk|legal|policy|\msales\M|recruit|adoption|evangelist|writer|tutor|annotat|content'
         and title !~* 'engineer|developer|scientist|research|architect|programmer|\mml\M|machine\s+learning|\mdata\M'
            then 'Not a tech role'
        when title ~* 'data\s+assistant|medical\s+data|\mai\s+trainers?\M'
            then 'Not a tech role'

        -- ---------------------------------------------------- QA & testing (before any "analyst" rule)
        when title ~* '\m(qa|qe|sdet|tester|testeur|testing)\M|quality\s+assurance|test\s*(automation|engineer|analyst|lead|manager|specialist|architect)|testautomat|softwaretest|\mautomation\s+tester'
         and title !~* 'data\s+quality|penetration|pen\s*test'
            then case when title ~* 'automat|sdet|selenium|cypress|playwright|appium|\mqe\M|software\s+(development\s+)?engineer\s+in\s+test'
                      then 'QA Automation Engineer' else 'QA / Test Engineer' end

        -- ------------------------------------------------- RPA / low-code platforms
        when title ~* '\mrpa\M|uipath|blue\s*prism|automation\s+anywhere|power\s+automate|low[\s-]?code|no[\s-]?code|outsystems|mendix|appian|\mpega(systems)?\M|intelligent\s+automation'
            then 'RPA / Low-code Developer'

        -- ------------------------------------------------- ERP & CRM (SAP, Salesforce, Dynamics, Oracle apps)
        when title ~* '\msap\M|\mabap\M|s/4\s*hana|salesforce|\msfdc\M|dynamics\s*365|dynamics\s+(crm|ax|nav)|\md365\M|business\s+central|oracle\s+(e-?business|ebs|fusion|erp|apps|hcm|scm|financials|cloud\s+(erp|hcm|financials))|servicenow|\modoo\M|power\s+(apps|platform|automate)|\m(erp|crm)\s+(developer|consultant|engineer|specialist|analyst|administrator|architect|lead|manager)'
         and title !~* 'data\s+(engineer|analyst|scientist)|analytics'
            then case when title ~* 'develop|engineer|\mabap\M|\mapex\M|technical|programmer|architect'
                      then 'ERP / CRM Developer' else 'ERP / CRM Consultant' end

        -- --------------------------------------------------------------- Data & AI
        when title ~* 'governance|data\s+protection|privacy|datenschutz|data\s+steward|data\s+catalog|\mgdpr\M'
            then 'Data Governance / Privacy'
        when title ~* 'analytics\s+engineer'
            then 'Analytics Engineer'
        when title ~* '\m(data|etl|elt|big\s+data|data\s+platform|data\s+pipeline|data\s+warehouse|data\s+integration)\s+(engineer|developer|architect)'
          or title ~* 'dateningenieur|data-?engineer|ingénieur\s+(de\s+)?données|ingeniero\s+de\s+datos|\metl\M'
          or (title ~* '\m(engineer|developer)\M'
              and title ~* 'data\s*(platforms?|systems?|infrastructure|ops|pipelines?|factory|extraction|lake|integration)|distributed\s+data|\mdataops\M|data\s*&\s*(ai\s+)?(platforms?|systems?)|data\s*/\s*software|data\s+enablement')
          or title ~* 'data\s+(and|&)\s+(ai|analytics)\s+(engineer|architect)'
            then 'Data Engineer'
        when title ~* '\mmlops\M|ml\s*ops|machine\s+learning\s+(platform|infrastructure|ops)\s+engineer|\mllmops\M'
            then 'MLOps Engineer'
        when title ~* '(research|applied)\s+scientist|\mai\s+researcher|research\s+engineer'
         and title !~* 'data\s+scien|clinical|market|ux|user'
            then 'AI Research Scientist / Engineer'
        when title ~* 'data\s+scien|\mscientist\M|scientifique\s+des\s+données|científico\s+de\s+datos'
            then 'Data Scientist'
        when title ~* 'machine\s+learning|\mml\M|\mmlops\M|deep\s+learning|\mnlp\M|computer\s+vision|\mai\s+(engineer|developer|researcher|architect)|\mllm\M|genai|\mai(\s*/\s*|\s+)(agent|safety|agentic|platform|applied|research)\s+(engineers?|developers?|scientists?)|agent(ic)?\s+(ai\s+)?engineer|ai\s+safety'
            then 'ML / AI Engineer'
        when title ~* 'business\s+intelligence|\mbi\M|power\s*bi|tableau|looker|\mreporting\s+(analyst|developer|engineer|specialist)'
            then 'BI Analyst / Developer'

        when title ~* '\mdba\M|\mdbre\M|database\s+(administrator|admin|engineer|reliability|specialist|architect)'
            then 'Database Administrator'

        -- --------------------------------------------------------------- Security
        when title ~* '\msecurity\M|\mcyber|\msoc\M|penetration|pen\s*test|\mappsec\M|\minfosec\M|\miam\s+(engineer|analyst)|threat|vulnerabilit|\mgrc\M'
            then 'Security Engineer / Analyst'

        -- --------------------------------------------------------------- UI / UX design
        when title ~* '(ux|ui|user\s+experience|user\s+interface|product|interaction|visual|web|service|motion)\s*(/\s*(ux|ui)\s*)?(designer|design\s+(lead|manager|director))|\mdesigner\M|design\s+systems?|\mux\s+(research|writer|lead|manager)|\mui\s*/\s*ux|\mux\s*/\s*ui'
            then case when title ~* 'research' then 'UX Researcher'
                      when title ~* '\mui\M|visual|web\s+designer|motion' and title !~* '\mux\M|product' then 'UI / Visual Designer'
                      else 'UX / Product Designer' end

        when title ~* '\mgis\M|arcgis|geospatial|\mqgis\M|geographic\s+information|remote\s+sensing|cartograph'
            then 'GIS Specialist'

        -- -------------------------------------- analyst roles (data track unless clearly IT)
        when title ~* '\m(systems?|it|technical|functional|software|application|erp|sap|crm|salesforce)\s+(business\s+)?analyst'
            then 'Systems / IT Analyst'
        when title ~* '(business|product|financial|finance|credit|risk|pricing|venture|clearing|operations|marketing|sales|revenue)\s+analyst'
            then 'Business / Domain Analyst'
        when title ~* 'data\s+analy|analytics|analyst|datenanaly|analyste|analista|insights?\s+(analyst|lead|manager|specialist)'
            then 'Data Analyst'

        -- ---------------------------------------------------------- Networks & telecom
        when title ~* 'telecom|\mrf\s+(engineer|planning|optimi[sz]ation)|\mran\M|\m(5g|lte|volte)\M|core\s+network|transmission\s+engineer|\mmicrowave\M|\mbss\M|\moss\M|packet\s+core'
            then 'Telecom Engineer'
        when title ~* 'network\s+(engineer|administrator|admin|architect|specialist|analyst|operations|support|lead)|\mnoc\M|\mvoip\M|unified\s+communications|wireless\s+engineer|\mccnp\M|\mccie\M|sd-?wan'
            then 'Network Engineer'

        -- --------------------------------------------------------- IT, DevOps & cloud
        when title ~* 'it\s+support|help\s*desk|service\s+desk|desktop\s+support|sys(tem)?\s*admin|system\s+administrator|systemadministrat|it\s+(administrator|technician|specialist|engineer)|fachinformatiker(?!.*anwendungsentwicklung)'
            then 'IT Support / Systems Admin'
        when title ~* '\msre\M|site\s+reliability'
            then 'Site Reliability Engineer'
        when title ~* '\m(devops|devsecops|dev\s*ops)\M|platform\s+engineer|release\s+engineer|build\s+engineer|kubernetes|infrastructure\s+engineer'
            then 'DevOps / Platform Engineer'
        when title ~* 'cloud\s+(engineer|architect|developer|specialist|consultant)|\m(aws|azure|gcp)\s+(engineer|architect|developer)|infrastructure|systems?\s+engineer'
            then 'Cloud / Infrastructure Engineer'

        -- -------------------------------------------------------- product & delivery
        when title ~* 'scrum\s+master|agile\s+coach|delivery\s+enabler|release\s+train\s+engineer'
            then 'Scrum Master / Agile Coach'
        when (title ~* '(technical|it|software|digital|engineering|tech|delivery|implementation)\s+(project|program|programme|delivery)\s+(manager|lead)|delivery\s+(manager|lead)|\mpmo\M'
              or (title ~* '(project|program|programme)\s+manager' and title ~* '\m(it|software|digital|technology|technical|cloud|platform|erp|data|ai)\M'))
         and title !~* 'construction|civil|mechanical|electrical|clinical|marketing|events?'
            then 'Technical Project / Program Manager'
        when title ~* 'product\s+(manager|owner|lead|director)|produktmanager|product\s+owner|chef\s+de\s+produit|head\s+of\s+product|\mcpo\M'
         and title !~* '\m(data|daten|données|datos|analytics|bi|ml|insights?)\M|product\s+(marketing|designer)'
            then 'Product Manager / Owner'
        when title ~* 'technical\s+writer|documentation\s+engineer|technical\s+(content|documentation)\s+(writer|specialist)|api\s+writer'
            then 'Technical Writer'

        -- ------------------------------------------------------- Software engineering
        when title ~* 'go[\s-]to[\s-]market\s+engineer|\mgtm\s+engineer'
            then 'Solutions / Support Engineer'
        when title ~* '(engineering|software|development|technical)\s+(manager|director)|\m(manager|director|head)\M[^,]*,\s*(software\s+)?engineering|director\s+of\s+(software\s+)?engineering|head\s+of\s+(engineering|software|technology|development)|\mcto\M|vp\s+(of\s+)?engineering|chief\s+technology'
            then 'Engineering Leadership'
        when title ~* 'blockchain|\mweb3\M|solidity|smart\s+contracts?|\mdefi\M'
            then 'Blockchain Engineer'
        -- mobile: cross-platform first ("Flutter Developer (iOS & Android)" is cross-platform)
        when title ~* 'flutter|react[\s-]+native|xamarin|\.net\s+maui|\mmaui\M|kotlin\s+multiplatform|\mkmp\M|ionic|cross[\s-]?platform\s+(mobile|app|developer|engineer)'
            then 'Cross-platform Mobile Engineer'
        -- native for both platforms: "Native Mobile Developer", "iOS & Android Engineer"
        when title ~* 'native\s+(mobile|app)|mobile\s+native|\mios\s*(&|and|/|\+)\s*android\M|\mandroid\s*(&|and|/|\+)\s*ios\M'
            then 'Native Mobile Engineer (iOS + Android)'
        when title ~* '\mios\M|swiftui|\mswift\s+(developer|engineer)|objective[\s-]?c|iphone'
         and title !~* '\mandroid\M'
            then 'iOS Engineer'
        when title ~* '\mandroid\M|jetpack\s+compose'
         and title !~* '\mios\M'
            then 'Android Engineer'
        when title ~* '\m(ios|android|mobile)\M|mobile\s+app'
            then 'Mobile Engineer'
        when title ~* 'robotics?\s+(software\s+)?(engineer|developer|programmer)|\mros2?\M|autonomous\s+(systems?|vehicles?|driving)\s+engineer|perception\s+engineer'
            then 'Robotics Engineer'
        when title ~* 'full[\s-]?stack|fullstack'
            then 'Full-Stack Engineer'
        when title ~* 'front[\s-]?end|frontend|\mui\s+(engineer|developer)|web\s+(developer|engineer)|javascript|typescript|\mreact\M|angular|\mvue\M|svelte|wordpress|shopify\s+developer'
            then 'Frontend Engineer'
        when title ~* 'back[\s-]?end|backend|\mapi\s+(engineer|developer)|\mjava\M|\.net\M|c#|\mgolang\M|\mgo\s+(engineer|developer)|\m(python|node(\.?js)?|php|ruby|rails|rust|scala|elixir|django|laravel|spring)\s+(engineer|developer)'
            then 'Backend Engineer'
        when title ~* 'embedded|firmware|\mc\+\+|\mrtos\M'
            then 'Embedded / Firmware Engineer'
        when title ~* 'game\s+(developer|engineer|programmer|designer)|gameplay|\munity\s+(developer|engineer|programmer)|\munreal\M|\m(ar|vr|xr)\s*/?\s*(vr|ar)?\s*(developer|engineer)|augmented\s+reality|virtual\s+reality|mixed\s+reality|metaverse'
            then 'Game / XR Developer'
        when title ~* '(solutions?|sales|pre-?sales|customer|support|integration|implementation|forward\s+deployed)\s+(engineer|architect|consultant)|technical\s+account\s+manager'
         and title !~* 'data|analytics'
            then 'Solutions / Support Engineer'
        when (title ~* '\m(engineer|developer|programmer|architect|coder|sdet)\M|entwickler|développeu|desarrollador|programador|ingeniero\s+de\s+software|ontwikkelaar|informatiker|software\s+(engineering|development)'
              and title !~* '\mdata\M|daten|données|datos')
          or title ~* 'software\s+(engineer|developer|engineering)'
            then 'Software Engineer'

        -- --------------------------------------- data-adjacent (only titles with data words)
        when title ~* '\m(data|daten|données|datos|analytics|bi|ai|ml|machine\s+learning|insights?|lakehouse|lakebase|edw|dwh|dbt)\M'
            then case
                when title ~* 'product\s+(manager|owner|marketing|lead|director|partner)'
                    then 'Data Product Management'
                when title ~* 'consultant|consulting|pre-?sales|presales|solutions?\s+(architect|engineer|consultant)|account\s+(executive|manager)|\msales\M|berater|practitioner|customer\s+(delivery|success)'
                    then 'Solutions / Consulting'
                when title ~* '\m(manager|director|head|vp|vice\s+president|chief|lead)\M|projektleit|project\s+(manager|lead)'
                    then 'Data Leadership'
                else 'Other data role'
            end

        -- ------------------------------------------------------------ other tech
        when title ~* 'product\s+(manager|owner)|produktmanager|chef\s+de\s+produit'
            then 'Product Manager / Owner'
        else 'Other tech role'
    end
$role$;
CREATE OR REPLACE FUNCTION core.seniority(title text) RETURNS text LANGUAGE sql IMMUTABLE AS $seniority$
SELECT
    case
        when title ~* '\m(intern|internship|werkstudent\w*|working\s+student|praktikant\w*|praktikum|stage|trainee|apprentice\w*|alternance|student)\M'
            then 'Intern / Student'
        when title ~* '\m(junior|jr|entry|graduate|grad)\M'
            then 'Junior'
        when title ~* '\m(head|director|vp|vice\s+president|chief)\M'
            then 'Head / Director'
        when title ~* '\m(lead|principal|staff)\M|(engineering|development|software|technical|data|qa|it)\s+manager|manager,?\s+(software\s+)?engineering'
            then 'Lead / Principal'
        when title ~* '\m(senior|sr|snr|architect)\M'
            then 'Senior'
        else 'Not stated'
    end
$seniority$;
CREATE TABLE IF NOT EXISTS core.role_track (
    role_family text PRIMARY KEY,
    track       text NOT NULL
);
DELETE FROM core.role_track;
INSERT INTO core.role_track (role_family, track) VALUES
    ('Data Engineer', 'Data & Analytics'),
    ('Analytics Engineer', 'Data & Analytics'),
    ('Data Analyst', 'Data & Analytics'),
    ('BI Analyst / Developer', 'Data & Analytics'),
    ('Data Scientist', 'Data & Analytics'),
    ('Business / Domain Analyst', 'Data & Analytics'),
    ('Data Governance / Privacy', 'Data & Analytics'),
    ('Database Administrator', 'Data & Analytics'),
    ('Data Product Management', 'Data & Analytics'),
    ('Solutions / Consulting', 'Data & Analytics'),
    ('Data Leadership', 'Data & Analytics'),
    ('Other data role', 'Data & Analytics'),
    ('GIS Specialist', 'Data & Analytics'),
    ('ML / AI Engineer', 'AI & Machine Learning'),
    ('MLOps Engineer', 'AI & Machine Learning'),
    ('AI Research Scientist / Engineer', 'AI & Machine Learning'),
    ('Software Engineer', 'Software Engineering'),
    ('Backend Engineer', 'Software Engineering'),
    ('Frontend Engineer', 'Software Engineering'),
    ('Full-Stack Engineer', 'Software Engineering'),
    ('Embedded / Firmware Engineer', 'Software Engineering'),
    ('Game / XR Developer', 'Software Engineering'),
    ('Robotics Engineer', 'Software Engineering'),
    ('Blockchain Engineer', 'Software Engineering'),
    ('Engineering Leadership', 'Software Engineering'),
    ('Native Mobile Engineer (iOS + Android)', 'Mobile Development'),
    ('iOS Engineer', 'Mobile Development'),
    ('Android Engineer', 'Mobile Development'),
    ('Cross-platform Mobile Engineer', 'Mobile Development'),
    ('Mobile Engineer', 'Mobile Development'),
    ('QA Automation Engineer', 'QA & Testing'),
    ('QA / Test Engineer', 'QA & Testing'),
    ('UX / Product Designer', 'UI/UX Design'),
    ('UI / Visual Designer', 'UI/UX Design'),
    ('UX Researcher', 'UI/UX Design'),
    ('DevOps / Platform Engineer', 'DevOps & Cloud'),
    ('Site Reliability Engineer', 'DevOps & Cloud'),
    ('Cloud / Infrastructure Engineer', 'DevOps & Cloud'),
    ('Security Engineer / Analyst', 'Security'),
    ('ERP / CRM Consultant', 'ERP & CRM'),
    ('ERP / CRM Developer', 'ERP & CRM'),
    ('RPA / Low-code Developer', 'ERP & CRM'),
    ('Network Engineer', 'Networks & Telecom'),
    ('Telecom Engineer', 'Networks & Telecom'),
    ('Product Manager / Owner', 'Product & Delivery'),
    ('Technical Project / Program Manager', 'Product & Delivery'),
    ('Scrum Master / Agile Coach', 'Product & Delivery'),
    ('IT Support / Systems Admin', 'IT & Support'),
    ('Systems / IT Analyst', 'IT & Support'),
    ('Solutions / Support Engineer', 'IT & Support'),
    ('Technical Writer', 'IT & Support'),
    ('Other tech role', 'IT & Support'),
    ('Not a tech role', 'Not tech');

-- The company boards the website's collection reads (extract_market, sources/market.py): one row per platform and
-- token. origin: radarak (sources/boards.csv), harvested (a board another source linked to), startup (a startup list's
-- careers page). A candidate becomes active once read; dead when its board is gone (404) or after 3 failures in a row.
-- One board per company domain (when known).
CREATE TABLE IF NOT EXISTS core.board (
    ats        text        NOT NULL,
    token      text        NOT NULL,
    company    text,
    domain     text,
    origin     text        NOT NULL,
    status     text        NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate', 'active', 'dead')),
    failures   integer     NOT NULL DEFAULT 0,
    jobs       integer,                -- its tech jobs at the last read
    checked_at timestamptz,
    last_ok_at timestamptz,
    note       text,
    added_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ats, token)
);
CREATE UNIQUE INDEX IF NOT EXISTS board_domain ON core.board (lower(domain)) WHERE domain IS NOT NULL;
-- the platforms sources/market.py reads (Workday came later: a board made before gets the wider check here)
ALTER TABLE core.board DROP CONSTRAINT IF EXISTS board_ats_check;
ALTER TABLE core.board ADD CONSTRAINT board_ats_check CHECK (ats IN ('greenhouse', 'lever', 'ashby', 'smartrecruiters', 'workable', 'recruitee', 'personio', 'workday'));
-- The startup directory (the website's jobs.startup: founder cards, and where the company boards come from), one row per
-- company domain, loaded by scripts/find_boards.py from the startup lists: YC's directory ('yc'), Wamda's portfolio
-- ('wamda'), your startups.csv ('owner'), the YC 154 remote and visa sheet ('yc_visa') and the remote-hiring list
-- ('remote_list'). The first list keeps its values; a later one only fills empty ones, except the flags, which turn true
-- when any list says so. board: the company board its pages name ('greenhouse:stripe', looked up once: looked_up_at).
CREATE TABLE IF NOT EXISTS core.startup (
    domain           text        PRIMARY KEY,
    company          text        NOT NULL,
    website          text,
    one_liner        text,
    industries       text[]      NOT NULL DEFAULT '{}',
    batch            text,
    location         text,
    region           text,
    team_size        integer,
    source           text        NOT NULL CHECK (source IN ('yc', 'wamda', 'flat6labs', 'f6s', 'wellfound', 'startuplist_africa',
                                                             'linkedin', 'owner', 'yc_visa', 'remote_list')),
    source_id        text,
    source_url       text,
    founder_name     text,
    founder_url      text,
    hiring           boolean     NOT NULL DEFAULT false,
    has_remote_roles boolean     NOT NULL DEFAULT false,
    has_internships  boolean     NOT NULL DEFAULT false,
    open_source      boolean     NOT NULL DEFAULT false,
    visa_friendly    boolean     NOT NULL DEFAULT false,
    priority         boolean     NOT NULL DEFAULT false,
    signal           text,
    careers_url      text,
    board            text,
    looked_up_at     timestamptz,
    first_seen       date        NOT NULL DEFAULT current_date,
    updated_at       timestamptz NOT NULL DEFAULT now()
);
-- the first lookups were kept in core.board_lookup (2026-10-17 only): moved here once
DO $$
BEGIN
    IF to_regclass('core.board_lookup') IS NOT NULL THEN
        INSERT INTO core.startup (domain, company, source, board, looked_up_at)
        SELECT domain, coalesce(company, domain), CASE origin WHEN 'yc' THEN 'yc' WHEN 'wamda' THEN 'wamda' ELSE 'owner' END,
               board, checked_at FROM core.board_lookup ON CONFLICT (domain) DO NOTHING;
        DROP TABLE core.board_lookup;
    END IF;
END $$;

-- Each day's new jobs (yours and the website's) and how many of them ask for each skill (match_skills), kept after the
-- jobs are deleted: the rising skills compare a skill's share of a week with the weeks before.
CREATE TABLE IF NOT EXISTS core.skill_day (
    day        date    NOT NULL,
    skill      text    NOT NULL,
    jobs       integer NOT NULL,  -- the day's new jobs asking for it
    total_jobs integer NOT NULL,  -- the day's new jobs, repeated on each row so a share needs no join
    PRIMARY KEY (day, skill)
);

-- The ECB's euro reference rates (fx.py): units of a currency per 1 euro, by day; the euro itself at 1.
CREATE TABLE IF NOT EXISTS raw.fx_rate (
    rate_date     date    NOT NULL,
    currency      text    NOT NULL,
    units_per_eur numeric NOT NULL,
    PRIMARY KEY (rate_date, currency)
);

-- Locations to countries and regions, for core.job's countries and regions (match_skills); reloaded on every run.
-- From Radarak's geo patterns (MIT, Copyright (c) 2026 Moamen Moussa; see LICENSE-THIRD-PARTY). country is empty for a
-- region-only pattern (EMEA, Europe, APAC ...).
CREATE TABLE IF NOT EXISTS core.geo_pattern (
    pattern text NOT NULL CHECK (('' ~* pattern) IS NOT NULL),
    country text,
    region  text NOT NULL
);
DELETE FROM core.geo_pattern;
INSERT INTO core.geo_pattern (pattern, country, region) VALUES
    ('\m(europe|european\s+union|eu|eea|european)\M', NULL, 'Europe'),
    ('\memea\M', NULL, 'Europe'),
    ('\memea\M', NULL, 'MENA'),
    ('\memea\M', NULL, 'Africa'),
    ('\mamericas\M', NULL, 'North America'),
    ('\mamericas\M', NULL, 'LATAM'),
    ('\mnorth\s+america\M', NULL, 'North America'),
    ('\m(latam|latin\s+america|south\s+america|central\s+america)\M', NULL, 'LATAM'),
    ('\m(apac|asia|asia[- ]pacific)\M', NULL, 'APAC'),
    ('\m(mena|middle\s+east|gcc)\M', NULL, 'MENA'),
    ('\mafrica\M', NULL, 'Africa'),
    ('\m(usa|us|u\.s\.a?\.?|united\s+states)\M|(^|[\s,])u\.s\.(\s|,|$)|san\s+francisco|san\s+leandro|santa\s+monica|redwood\s+city|bay\s+area|\msf\M|new\s+york|\mnyc\M|seattle|austin|boston|chicago|los\s+angeles|denver|atlanta|washington,?\s+d\.?c|,\s*(ny|wa|tx|ma|il|nj|ga|nc|or)\s*(,\s*(usa|us))?\s*$', 'United States', 'North America'),
    ('\m(california|texas|florida|massachusetts|illinois|colorado|virginia|north\s+carolina|oregon|arizona|utah|pennsylvania|ohio|michigan|minnesota|new\s+jersey|washington|tennessee|missouri|wisconsin|maryland|indiana|nevada|connecticut)\M|\m(bellevue|mountain\s+view|palo\s+alto|menlo\s+park|sunnyvale|san\s+jose|santa\s+clara|cupertino|redmond|plano|dallas|houston|san\s+diego|portland|miami|philadelphia|pittsburgh|raleigh|salt\s+lake|phoenix|minneapolis|detroit|irvine|nashville|charlotte)\M', 'United States', 'North America'),
    ('\m(canada|toronto|vancouver|montr[eé]al|ottawa|calgary)\M', 'Canada', 'North America'),
    ('\m(mexico|méxico|cdmx|guadalajara|monterrey)\M', 'Mexico', 'LATAM'),
    ('\m(brazil|brasil|s[aã]o\s+paulo|rio\s+de\s+janeiro)\M', 'Brazil', 'LATAM'),
    ('\m(argentina|buenos\s+aires)\M', 'Argentina', 'LATAM'),
    ('\m(colombia|bogot[aá]|medell[ií]n)\M', 'Colombia', 'LATAM'),
    ('\m(chile|santiago)\M', 'Chile', 'LATAM'),
    ('\m(jamaica|kingston,\s*jamaica)\M', 'Jamaica', 'LATAM'),
    ('\m(dominican\s+republic|santo\s+domingo)\M', 'Dominican Republic', 'LATAM'),
    ('\m(costa\s+rica|san\s+jos[eé],\s*costa)\M', 'Costa Rica', 'LATAM'),
    ('\m(panama)\M', 'Panama', 'LATAM'),
    ('\m(bermuda)\M', 'Bermuda', 'LATAM'),
    ('\m(antigua(\s+and\s+barbuda)?)\M', 'Antigua and Barbuda', 'LATAM'),
    ('\m(barbados)\M', 'Barbados', 'LATAM'),
    ('\m(bahamas)\M', 'Bahamas', 'LATAM'),
    ('\m(trinidad(\s+and\s+tobago)?)\M', 'Trinidad and Tobago', 'LATAM'),
    ('\m(puerto\s+rico)\M', 'Puerto Rico', 'LATAM'),
    ('\m(guatemala)\M', 'Guatemala', 'LATAM'),
    ('\m(el\s+salvador)\M', 'El Salvador', 'LATAM'),
    ('\m(honduras)\M', 'Honduras', 'LATAM'),
    ('\m(nicaragua)\M', 'Nicaragua', 'LATAM'),
    ('\m(belize)\M', 'Belize', 'LATAM'),
    ('\m(cayman(\s+islands)?)\M', 'Cayman Islands', 'LATAM'),
    ('\m(peru|lima)\M', 'Peru', 'LATAM'),
    ('\m(uruguay|montevideo)\M', 'Uruguay', 'LATAM'),
    ('\m(ecuador|quito)\M', 'Ecuador', 'LATAM'),
    ('\m(venezuela|caracas)\M', 'Venezuela', 'LATAM'),
    ('\m(bolivia)\M', 'Bolivia', 'LATAM'),
    ('\m(paraguay)\M', 'Paraguay', 'LATAM'),
    ('\m(germany|deutschland|berlin|m[uü]nchen|munich|hamburg|k[oö]ln|cologne|frankfurt|stuttgart|d[uü]sseldorf|karlsruhe|kassel|mainz|offenburg|leipzig|dresden|n[uü]rnberg|nuremberg|hannover|bremen|bonn|essen|dortmund|heidelberg|mannheim|freiburg|baden-w[uü]rttemberg|bavaria|bayern)\M', 'Germany', 'Europe'),
    ('\m(france|paris|lyon|lille|montpellier|toulouse|marseille|bordeaux|nantes|nice|strasbourg|rennes)\M', 'France', 'Europe'),
    ('\m(united\s+kingdom|uk|u\.k\.|gb|great\s+britain|england|scotland|wales|london|manchester|edinburgh|glasgow|bristol|cambridge|oxford|leeds|birmingham|westminster|belfast|cardiff|swansea|newcastle|liverpool|nottingham|sheffield|reading)\M', 'United Kingdom', 'Europe'),
    ('\m(netherlands|nederland|holland|amsterdam|rotterdam|utrecht|eindhoven|the\s+hague|den\s+haag)\M', 'Netherlands', 'Europe'),
    ('\m(spain|españa|espana|madrid|barcelona|valencia|sevilla|seville|m[aá]laga|bilbao)\M', 'Spain', 'Europe'),
    ('\m(portugal|lisbon|lisboa|porto|braga)\M', 'Portugal', 'Europe'),
    ('\m(ireland|dublin|cork|galway)\M', 'Ireland', 'Europe'),
    ('\m(switzerland|schweiz|suisse|z[uü]rich|geneva|gen[eè]ve|basel|bern|lausanne|lugano|mendrisio|zug)\M', 'Switzerland', 'Europe'),
    ('\m(austria|[oö]sterreich|vienna|wien|graz|linz|salzburg)\M', 'Austria', 'Europe'),
    ('\m(belgium|belgique|brussels|bruxelles|antwerp|ghent)\M', 'Belgium', 'Europe'),
    ('\m(luxembourg)\M', 'Luxembourg', 'Europe'),
    ('\m(italy|italia|milan|milano|rome|roma|turin|torino)\M', 'Italy', 'Europe'),
    ('\m(poland|polska|warsaw|warszawa|krak[oó]w|wroc[lł]aw|gda[nń]sk|pozna[nń])\M', 'Poland', 'Europe'),
    ('\m(czech|czechia|prague|praha|brno)\M', 'Czechia', 'Europe'),
    ('\m(romania|bucharest|cluj)\M', 'Romania', 'Europe'),
    ('\m(hungary|budapest)\M', 'Hungary', 'Europe'),
    ('\m(greece|athens)\M', 'Greece', 'Europe'),
    ('\m(sweden|stockholm|gothenburg|malm[oö])\M', 'Sweden', 'Europe'),
    ('\m(denmark|copenhagen|aarhus)\M', 'Denmark', 'Europe'),
    ('\m(norway|oslo|bergen)\M', 'Norway', 'Europe'),
    ('\m(finland|helsinki|espoo|tampere)\M', 'Finland', 'Europe'),
    ('\m(estonia|tallinn)\M', 'Estonia', 'Europe'),
    ('\m(lithuania|vilnius)\M', 'Lithuania', 'Europe'),
    ('\m(latvia|riga)\M', 'Latvia', 'Europe'),
    ('\m(serbia|belgrade)\M', 'Serbia', 'Europe'),
    ('\m(croatia|zagreb)\M', 'Croatia', 'Europe'),
    ('\m(bulgaria|sofia)\M', 'Bulgaria', 'Europe'),
    ('\m(ukraine|kyiv|kiev|lviv)\M', 'Ukraine', 'Europe'),
    ('\m(cyprus|limassol|nicosia)\M', 'Cyprus', 'Europe'),
    ('\m(malta)\M', 'Malta', 'Europe'),
    ('\m(turkey|türkiye|turkiye|istanbul|ankara)\M', 'Turkey', 'MENA'),
    ('\m(egypt|cairo|alexandria|giza)\M|\m(nasr|nasser)\s+city\M', 'Egypt', 'MENA'),
    ('\m(uae|u\.a\.e|united\s+arab\s+emirates|dubai|abu\s+dhabi)\M', 'United Arab Emirates', 'MENA'),
    ('\m(saudi|ksa|riyadh|jeddah|dammam)\M', 'Saudi Arabia', 'MENA'),
    ('\m(qatar|doha)\M', 'Qatar', 'MENA'),
    ('\m(bahrain|manama)\M', 'Bahrain', 'MENA'),
    ('\m(kuwait)\M', 'Kuwait', 'MENA'),
    ('\m(oman|muscat)\M', 'Oman', 'MENA'),
    ('\m(jordan|amman)\M', 'Jordan', 'MENA'),
    ('\m(lebanon|beirut)\M', 'Lebanon', 'MENA'),
    ('\m(morocco|casablanca|rabat)\M', 'Morocco', 'MENA'),
    ('\m(tunisia|tunis)\M', 'Tunisia', 'MENA'),
    ('\m(israel|tel\s+aviv|jerusalem|haifa)\M', 'Israel', 'MENA'),
    ('\m(nigeria|lagos|abuja)\M', 'Nigeria', 'Africa'),
    ('\m(kenya|nairobi)\M', 'Kenya', 'Africa'),
    ('\m(south\s+africa|cape\s+town|johannesburg)\M', 'South Africa', 'Africa'),
    ('\m(ghana|accra)\M', 'Ghana', 'Africa'),
    ('\m(india|bangalore|bengaluru|hyderabad|pune|mumbai|delhi|gurgaon|gurugram|noida|chennai|kolkata)\M', 'India', 'APAC'),
    ('\m(pakistan|karachi|lahore|islamabad)\M', 'Pakistan', 'APAC'),
    ('\m(singapore)\M', 'Singapore', 'APAC'),
    ('\m(japan|tokyo|osaka)\M', 'Japan', 'APAC'),
    ('\m(south\s+korea|korea|seoul)\M', 'South Korea', 'APAC'),
    ('\m(china|beijing|shanghai|shenzhen)\M', 'China', 'APAC'),
    ('\m(hong\s+kong)\M', 'Hong Kong', 'APAC'),
    ('\m(taiwan|taipei)\M', 'Taiwan', 'APAC'),
    ('\m(philippines|manila)\M', 'Philippines', 'APAC'),
    ('\m(vietnam|ho\s+chi\s+minh|hanoi)\M', 'Vietnam', 'APAC'),
    ('\m(indonesia|jakarta)\M', 'Indonesia', 'APAC'),
    ('\m(malaysia|kuala\s+lumpur)\M', 'Malaysia', 'APAC'),
    ('\m(thailand|bangkok)\M', 'Thailand', 'APAC'),
    ('\m(australia|sydney|melbourne|brisbane|perth)\M', 'Australia', 'APAC'),
    ('\m(new\s+zealand|auckland|wellington)\M', 'New Zealand', 'APAC'),
    ('(القاهرة|الجيزة|الإسكندرية|الاسكندرية|مصر)', 'Egypt', 'MENA'),
    ('(دبي|أبو ظبي|أبوظبي|ابوظبي|الشارقة|عجمان|الإمارات|الامارات)', 'United Arab Emirates', 'MENA'),
    ('(الرياض|جدة|الدمام|الظهران|الخبر|مكة|المدينة المنورة|السعودية)', 'Saudi Arabia', 'MENA'),
    ('(الدوحة|قطر)', 'Qatar', 'MENA'),
    ('(الكويت)', 'Kuwait', 'MENA'),
    ('(المنامة|البحرين)', 'Bahrain', 'MENA'),
    ('(مسقط|سلطنة عمان|سلطنة عُمان)', 'Oman', 'MENA'),
    ('(عمّان|الأردن|الاردن)', 'Jordan', 'MENA');
-- A job first seen over 4 days ago (config.KEEP_DAYS) is archived (core.job_archive), then deleted by transform with its raw postings,
-- unless you noted or applied to it; only its key stays here, so a job still posted later is never
-- stored or emailed again.
CREATE TABLE IF NOT EXISTS core.job_seen (
    job_key text PRIMARY KEY
);

-- The jobs transform removes are archived here first, whole (title, company, link, description, Claude's score and
-- why): one row per job_id, `why` says which removal took it. Nothing deletes from here; the tracker's jobs and the
-- emails never read it.
CREATE TABLE IF NOT EXISTS core.job_archive (
    job_id      bigint PRIMARY KEY,
    job_key     text        NOT NULL,
    title       text        NOT NULL,
    company     text        NOT NULL,
    location    text        NOT NULL,
    place       text        NOT NULL,
    source      text        NOT NULL,
    job_url     text        NOT NULL,
    role        text        NOT NULL,
    date_posted date,
    posted_at   timestamptz,
    description text,
    company_size text,
    first_seen  date        NOT NULL,
    score       smallint,
    reason      text,
    why         text        NOT NULL,   -- 'aged' (first seen over KEEP_DAYS ago) or 'out of scope' (the rules changed)
    archived_on date        NOT NULL DEFAULT current_date
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
-- 'new' is stored when a job still marked new has a note; warehouses made before that get the wider check here,
-- once: dropping a constraint that is already right locks core.application and could deadlock with the tracker.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'core.application'::regclass
                   AND conname = 'application_status_check' AND pg_get_constraintdef(oid) LIKE '%''new''%') THEN
        ALTER TABLE core.application DROP CONSTRAINT IF EXISTS application_status_check;
        ALTER TABLE core.application ADD CONSTRAINT application_status_check
            CHECK (status IN ('new', 'saved', 'applied', 'interview', 'offer', 'rejected', 'ignored'));
    END IF;
END $$;

-- Your skills (Data Engineering, BI & Analytics and Generative AI). Reloaded on every run, so this
-- list is the only place to change them.
CREATE TABLE IF NOT EXISTS core.skill (
    skill   text PRIMARY KEY,
    track   text NOT NULL CHECK (track IN ('Data Engineering', 'BI & Analytics', 'Generative AI')),
    pattern text NOT NULL  -- Postgres regular expression, matched case-insensitively (~*)
);
-- 'BI & Analytics' came later; warehouses made before that get the wider check here.
ALTER TABLE core.skill DROP CONSTRAINT IF EXISTS skill_track_check;
ALTER TABLE core.skill ADD CONSTRAINT skill_track_check
    CHECK (track IN ('Data Engineering', 'BI & Analytics', 'Generative AI'));
-- Every tech track's skills came later (2026-10, for the website): track stays one of your three, empty for the rest;
-- category groups all of them (Language, Cloud, Testing ...). A pattern that is not a valid regular expression is refused
-- here, so it fails this step instead of match_skills.
ALTER TABLE core.skill ALTER COLUMN track DROP NOT NULL;
ALTER TABLE core.skill ADD COLUMN IF NOT EXISTS category text;
ALTER TABLE core.skill DROP CONSTRAINT IF EXISTS skill_pattern_check;
ALTER TABLE core.skill ADD CONSTRAINT skill_pattern_check CHECK (('' ~* pattern) IS NOT NULL);
DELETE FROM core.skill;
INSERT INTO core.skill (track, skill, pattern, category) VALUES
    ('Data Engineering', 'SQL', '\msql\M', 'Language'),
    ('Data Engineering', 'SQL Server', 'sql server|mssql|t-sql|tsql', 'Database'),
    ('Data Engineering', 'Data modeling', 'data model|\merd\M|entity relationship|normali[sz]ation', 'Practice'),
    ('Data Engineering', 'Data warehousing', 'data warehous|\mdwh\M|\molap\M', 'Warehouse / Lakehouse'),
    ('Data Engineering', 'Star schema / SCD', 'star[- ]schema|snowflake schema|dimensional model|fact table|\mscd\M|slowly changing', 'Practice'),
    ('Data Engineering', 'ETL / ELT', '\metl\M|\melt\M', 'Ingestion'),
    ('Data Engineering', 'Data lake / lakehouse', 'data lake|lakehouse|delta lake|medallion', 'Warehouse / Lakehouse'),
    ('Data Engineering', 'Query optimization', 'query optimi|query tuning|performance tuning|execution plan|indexing', 'Database'),
    ('Data Engineering', 'dbt', '\mdbt\M', 'Transformation'),
    ('Data Engineering', 'Data quality', 'data quality|data validation|great expectations', 'Practice'),
    ('Data Engineering', 'Data governance', 'data governance|data lineage|data catalog|metadata management', 'Practice'),
    ('Data Engineering', 'NoSQL', 'nosql|mongo|cassandra|dynamodb', 'Database'),
    ('Data Engineering', 'Python', '\mpython\M', 'Language'),
    ('Data Engineering', 'Pandas / NumPy', 'pandas|numpy', 'Processing'),
    ('Data Engineering', 'REST APIs', 'rest(ful)? apis?|api integration|postman', 'Backend'),
    ('Data Engineering', 'Azure', '\mazure\M', 'Cloud'),
    ('Data Engineering', 'Azure Data Factory', 'data factory|\madf\M', 'Ingestion'),
    ('Data Engineering', 'Databricks', 'databricks', 'Warehouse / Lakehouse'),
    ('Data Engineering', 'Azure Data Lake / Blob', '\madls|data lake storage|blob storage', 'Cloud'),
    ('Data Engineering', 'Spark', '\m(py)?spark\M', 'Processing'),
    ('Data Engineering', 'Hadoop / HDFS', 'hadoop|\mhdfs\M|mapreduce|\mhive\M', 'Processing'),
    ('Data Engineering', 'Kafka / streaming', 'kafka|stream processing|real-time data|event hub', 'Streaming'),
    ('Data Engineering', 'Airflow', 'airflow', 'Orchestration'),
    ('Data Engineering', 'SSIS', '\mssis\M', 'Ingestion'),
    ('Data Engineering', 'Informatica', 'informatica', 'Ingestion'),
    ('Data Engineering', 'Linux / shell', 'linux|shell script|\mbash\M', 'DevOps'),
    ('Data Engineering', 'Docker', 'docker', 'DevOps'),
    ('Data Engineering', 'Git', '\mgit\M|github|gitlab', 'DevOps'),
    ('Data Engineering', 'AWS', '\maws\M|amazon web services|redshift', 'Cloud'),
    ('Data Engineering', 'GCP / BigQuery', '\mgcp\M|google cloud|bigquery', 'Cloud'),
    ('BI & Analytics', 'Power BI', 'power ?bi', 'BI / Visualization'),
    ('BI & Analytics', 'DAX', '\mdax\M', 'BI / Visualization'),
    ('BI & Analytics', 'Power Query', 'power query', 'BI / Visualization'),
    ('BI & Analytics', 'Row-level security (RLS)', 'row[- ]level security|\mrls\M', 'BI / Visualization'),
    ('BI & Analytics', 'Incremental refresh', 'incremental refresh', 'BI / Visualization'),
    ('BI & Analytics', 'Report interactions', 'drill[- ]?(through|down)|cross[- ]filter|bookmarks|slicers', 'BI / Visualization'),
    ('BI & Analytics', 'Dashboards / data visualization', 'dashboard|data visuali[sz]ation', 'BI / Visualization'),
    ('BI & Analytics', 'KPIs', '\mkpis?\M|key performance indicator', 'BI / Visualization'),
    ('BI & Analytics', 'Excel', '\mexcel\M', 'BI / Visualization'),
    ('BI & Analytics', 'Advanced Excel (pivots, VBA)', 'pivot ?tables?|power pivot|\mvba\M|[xv]lookup|macros', 'BI / Visualization'),
    ('BI & Analytics', 'Tableau', 'tableau', 'BI / Visualization'),
    ('BI & Analytics', 'Looker / Qlik', 'looker|qlik', 'BI / Visualization'),
    ('BI & Analytics', 'SSRS / SSAS', '\mssrs\M|\mssas\M', 'BI / Visualization'),
    ('BI & Analytics', 'Statistics / analysis', 'statistic|trend analysis|variance analysis|hypothesis|a/b test', 'Practice'),
    ('BI & Analytics', 'Segmentation (RFM, cohorts)', 'segmentation|\mrfm\M|cohort', 'Practice'),
    ('BI & Analytics', 'Data cleaning', 'data clean|data cleansing|data preparation|data wrangling', 'Practice'),
    ('BI & Analytics', 'Reconciliation', 'reconcil', 'Practice'),
    ('BI & Analytics', 'Requirements / stakeholders', 'requirements gathering|business requirements|stakeholder', 'Product'),
    ('BI & Analytics', 'Reporting automation', 'reporting automation|report automation|automated report', 'BI / Visualization'),
    ('Generative AI', 'LLMs', '\mllms?\M|large language model', 'ML / AI'),
    ('Generative AI', 'Generative AI', 'generative ai|\mgen ?ai\M', 'ML / AI'),
    ('Generative AI', 'Prompt engineering', 'prompt engineering|prompt design|few-shot', 'ML / AI'),
    ('Generative AI', 'RAG', '\mrag\M|retrieval[- ]augmented', 'ML / AI'),
    ('Generative AI', 'Embeddings / semantic search', 'embedding|semantic search|vector search|similarity search|hybrid search', 'ML / AI'),
    ('Generative AI', 'Vector databases', 'vector (db|database|store)|qdrant|pinecone|weaviate|pgvector|faiss|milvus|chroma', 'ML / AI'),
    ('Generative AI', 'LangChain / LangGraph', 'langchain|langgraph', 'ML / AI'),
    ('Generative AI', 'AI agents', 'agentic|\mai agents?\M|multi-agent|autonomous agent', 'ML / AI'),
    ('Generative AI', 'Tool / function calling', 'function calling|tool calling', 'ML / AI'),
    ('Generative AI', 'MCP', 'model context protocol|\mmcp\M', 'ML / AI'),
    ('Generative AI', 'OpenAI / Gemini APIs', 'openai|\mgpt|gemini|anthropic|claude', 'ML / AI'),
    ('Generative AI', 'Hugging Face / Transformers', 'hugging ?face|transformer', 'ML / AI'),
    ('Generative AI', 'Fine-tuning (LoRA, PEFT)', 'fine-?tun|\mlora\M|qlora|\mpeft\M', 'ML / AI'),
    ('Generative AI', 'LLM evaluation', 'ragas|deepeval|llm evaluation|hallucination', 'ML / AI'),
    ('Generative AI', 'LLMOps / MLflow', 'llmops|mlops|mlflow|langsmith|langfuse', 'ML / AI'),
    ('Generative AI', 'Model serving (Ollama, vLLM)', 'ollama|vllm|model serving|inference', 'ML / AI'),
    ('Generative AI', 'NLP', '\mnlp\M|natural language|named entity|\mner\M', 'ML / AI'),
    ('Generative AI', 'OCR / document AI', '\mocr\M|document (ai|processing|parsing)|docling', 'ML / AI'),
    ('Generative AI', 'Multimodal / vision', 'multimodal|vision-language|\mvlms?\M|computer vision|speech-to-text|text-to-speech', 'ML / AI'),
    ('Generative AI', 'FastAPI', 'fastapi', 'Backend'),
    ('Generative AI', 'Pydantic', 'pydantic', 'Backend'),
    ('Generative AI', 'Async Python', 'asyncio|aiohttp', 'Backend'),
    ('Generative AI', 'Deep learning (PyTorch, TensorFlow)', 'pytorch|tensorflow|deep learning|neural network', 'ML / AI'),
    ('Generative AI', 'Machine learning', 'machine learning|\mml\M|scikit|sklearn', 'ML / AI'),
    ('Generative AI', 'Diffusion / image generation', 'stable diffusion|diffusion model|dall-?e|image generation', 'ML / AI'),
    ('Generative AI', 'GenAI security', 'prompt injection|guardrail|jailbreak', 'ML / AI'),
    -- From Radarak's skills (MIT, Copyright (c) 2026 Moamen Moussa; see LICENSE-THIRD-PARTY): every tech track, no track of yours
    (NULL, 'Scala', '\mscala\M', 'Language'),
    (NULL, 'Java', '\mjava\M', 'Language'),
    (NULL, 'R', '\mR\s+(programming|language)\M|\mR\s*(,|or|and|/)\s*(python|sql)\M|\m(python|sql)\s*(,|or|and|/)\s*R\M', 'Language'),
    (NULL, 'Flink', '\mflink\M', 'Processing'),
    (NULL, 'Trino / Presto', '\mtrino\M|\mpresto\M', 'Processing'),
    (NULL, 'Dagster', '\mdagster\M', 'Orchestration'),
    (NULL, 'Prefect', '\mprefect\M', 'Orchestration'),
    (NULL, 'Fivetran', '\mfivetran\M', 'Ingestion'),
    (NULL, 'Airbyte', '\mairbyte\M', 'Ingestion'),
    (NULL, 'AWS Glue', 'aws\s+glue|\mglue\s+(jobs?|etl|catalog)', 'Ingestion'),
    (NULL, 'Snowflake', '\msnowflake\M', 'Warehouse / Lakehouse'),
    (NULL, 'Azure Synapse', '\msynapse\M', 'Warehouse / Lakehouse'),
    (NULL, 'Microsoft Fabric', 'microsoft\s+fabric|\mms\s+fabric\M', 'Warehouse / Lakehouse'),
    (NULL, 'ClickHouse', '\mclickhouse\M', 'Warehouse / Lakehouse'),
    (NULL, 'DuckDB', '\mduckdb\M', 'Warehouse / Lakehouse'),
    (NULL, 'Iceberg', '\miceberg\M', 'Table format'),
    (NULL, 'PostgreSQL', '\mpostgres(ql)?\M', 'Database'),
    (NULL, 'MySQL', '\mmysql\M', 'Database'),
    (NULL, 'Oracle', '\moracle\M', 'Database'),
    (NULL, 'Elasticsearch', '\melastic(search)?\M|\mopensearch\M', 'Database'),
    (NULL, 'Redis', '\mredis\M', 'Database'),
    (NULL, 'Kubernetes', '\mkubernetes\M|\mk8s\M', 'DevOps'),
    (NULL, 'Terraform', '\mterraform\M', 'DevOps'),
    (NULL, 'CI/CD', 'ci\s*/\s*cd|\mcicd\M|continuous\s+integration', 'DevOps'),
    (NULL, 'Data Vault', 'data\s+vault', 'Practice'),
    (NULL, 'JavaScript', '\mjavascript\M|\mecmascript\M|\mes6\M', 'Language'),
    (NULL, 'TypeScript', '\mtypescript\M', 'Language'),
    (NULL, 'Go', '\mgolang\M|\mgo\s+(developer|engineer|programming|language|microservices|services)\M|(python|java|rust|kotlin|typescript|c\+\+|node(\.?js)?)\s*(,|/|and|or)\s*go\M|\mgo\s*(,|/)\s*(python|java|rust|kotlin|typescript|c\+\+)', 'Language'),
    (NULL, 'Rust', '\mrust\M', 'Language'),
    (NULL, 'C#', '(^|[^a-z])c#', 'Language'),
    (NULL, 'C++', '(^|[^a-z])c\+\+', 'Language'),
    (NULL, 'PHP', '\mphp\M', 'Language'),
    (NULL, 'Ruby', '\mruby\M', 'Language'),
    (NULL, 'Kotlin', '\mkotlin\M', 'Language'),
    (NULL, 'Swift', '\mswiftui\M|\mswift\M(?=[^.]{0,80}\m(ios|xcode|uikit|kotlin|objective-c|apple)\M)|\m(ios|xcode|uikit|objective-c)\M[^.]{0,80}\mswift\M', 'Language'),
    (NULL, 'Dart', '\mdart\M', 'Language'),
    (NULL, 'HTML/CSS', '\mhtml5?\M|\mcss3?\M|\msass\M|\mscss\M', 'Frontend'),
    (NULL, 'React', '\mreact(\.?js)?\M(?!\s+(to|quickly|fast|swiftly|accordingly|appropriately|promptly|positively|calmly)\M)(?!\s+native)', 'Frontend'),
    (NULL, 'Angular', '\mangular(js)?\M', 'Frontend'),
    (NULL, 'Vue.js', '\mvue(\.?js)?\M|\mnuxt\M', 'Frontend'),
    (NULL, 'Next.js', '\mnext\.?js\M', 'Frontend'),
    (NULL, 'Node.js', '\mnode\.?js\M|\mnodejs\M|\mexpress\.?js\M|\mnestjs\M|\mnode\s*(,|/)\s*(typescript|javascript|react)|\m(typescript|javascript|react)\s*(,|/|and)\s*node\M', 'Backend'),
    (NULL, 'Java Spring', 'spring\s+(boot|framework|cloud|mvc|data|security)', 'Backend'),
    (NULL, '.NET', '(^|[\s(,/])\.net(\s+core)?\M|\masp\.net\M|\mdotnet\M|\mvb\.net\M', 'Backend'),
    (NULL, 'Django', '\mdjango\M', 'Backend'),
    (NULL, 'Laravel', '\mlaravel\M|\msymfony\M', 'Backend'),
    (NULL, 'GraphQL', '\mgraphql\M', 'Backend'),
    (NULL, 'Microservices', 'micro-?services?', 'Backend'),
    (NULL, 'iOS', '\mios\M', 'Mobile'),
    (NULL, 'Android', '\mandroid\M', 'Mobile'),
    (NULL, 'React Native', 'react\s+native', 'Mobile'),
    (NULL, 'Flutter', '\mflutter\M', 'Mobile'),
    (NULL, 'Selenium', '\mselenium\M|\mwebdriver\M', 'Testing'),
    (NULL, 'Cypress', '\mcypress\M', 'Testing'),
    (NULL, 'Playwright', '\mplaywright\M', 'Testing'),
    (NULL, 'Pytest', '\mpytest\M', 'Testing'),
    (NULL, 'JMeter/k6', '\mjmeter\M|\mgatling\M|\mk6\M|\mlocust\M|load\s+testing|performance\s+testing', 'Testing'),
    (NULL, 'Test automation', 'test\s+automation|automated\s+(tests?|testing)|automation\s+framework', 'Testing'),
    (NULL, 'Manual testing', 'manual\s+(testing|tests?|qa)|exploratory\s+testing|test\s+cases?', 'Testing'),
    (NULL, 'Figma', '\mfigma\M|\mfigjam\M', 'Design'),
    (NULL, 'Adobe XD / Creative Suite', 'adobe\s+(xd|creative|photoshop|illustrator|after\s+effects)|\mphotoshop\M|\millustrator\M', 'Design'),
    (NULL, 'User research', 'user\s+research|usability\s+test\w*|user\s+interviews?|\mux\s+research', 'Design'),
    (NULL, 'Design systems', 'design\s+systems?|component\s+librar(y|ies)', 'Design'),
    (NULL, 'Accessibility', '\maccessibility\M|\mwcag\M|\ma11y\M', 'Design'),
    (NULL, 'Jenkins', '\mjenkins\M', 'DevOps'),
    (NULL, 'GitHub Actions', 'github\s+actions|gitlab\s+ci|circleci|argo\s*cd|\margocd\M', 'DevOps'),
    (NULL, 'Ansible', '\mansible\M|\mpuppet\M|\mchef\s+(infra|automate)\M', 'DevOps'),
    (NULL, 'Prometheus/Grafana', '\mprometheus\M|\mgrafana\M|\mdatadog\M|\mnew\s+relic\M|\mopentelemetry\M', 'DevOps'),
    (NULL, 'Serverless', '\mserverless\M|aws\s+lambda|azure\s+functions|cloud\s+functions', 'DevOps'),
    (NULL, 'OWASP / AppSec', '\mowasp\M|\mappsec\M|secure\s+coding|\msast\M|\mdast\M', 'Security'),
    (NULL, 'Penetration testing', 'penetration\s+test\w*|\mpentest\w*|ethical\s+hacking|burp\s+suite|\mmetasploit\M', 'Security'),
    (NULL, 'SIEM / SOC', '\msiem\M|\msplunk\M|\mqradar\M|security\s+operations|incident\s+response', 'Security'),
    (NULL, 'IAM', 'identity\s+and\s+access|\miam\M|\mokta\M|\moauth\M|\msso\M', 'Security'),
    (NULL, 'Agile / Scrum', '\magile\M|\mscrum\M|\mkanban\M', 'Practice'),
    (NULL, 'Jira', '\mjira\M', 'Practice'),
    (NULL, 'System design', 'system\s+design|distributed\s+systems|scalab(le|ility)\s+(systems?|architecture)', 'Practice'),
    (NULL, 'Testing practices (TDD)', '\mtdd\M|test[\s-]driven|unit\s+tests?|integration\s+tests?', 'Practice'),
    (NULL, 'SAP S/4HANA', '\msap\M|s/4\s*hana|sap\s+hana', 'ERP & CRM'),
    (NULL, 'ABAP', '\mabap\M', 'ERP & CRM'),
    (NULL, 'SAP FICO', '\mfico\M|sap\s+fi(/|\s*-\s*)co', 'ERP & CRM'),
    (NULL, 'Salesforce', 'salesforce|\msfdc\M|lightning\s+web\s+components?|\mlwc\M', 'ERP & CRM'),
    (NULL, 'Microsoft Dynamics 365', 'dynamics\s*365|dynamics\s+(crm|ax|nav)|\md365\M|business\s+central', 'ERP & CRM'),
    (NULL, 'Oracle ERP / EBS', 'oracle\s+(e-?business|ebs|fusion|erp|cloud\s+erp|hcm|scm|financials)', 'ERP & CRM'),
    (NULL, 'ServiceNow', 'servicenow', 'ERP & CRM'),
    (NULL, 'Odoo', '\modoo\M', 'ERP & CRM'),
    (NULL, 'Power Platform', 'power\s+(apps|automate|platform)', 'ERP & CRM'),
    (NULL, 'Cisco / CCNA', '\mcisco\M|\mccn[ap]\M|\mccie\M', 'Networking'),
    (NULL, 'Firewalls', '\mfirewalls?\M|fortinet|fortigate|palo\s+alto|checkpoint\s+firewall|check\s+point\s+(firewall|ngfw)', 'Networking'),
    (NULL, 'VMware', 'vmware|vsphere|\mesxi\M', 'DevOps'),
    (NULL, 'Windows Server / AD', 'windows\s+server|active\s+directory', 'DevOps'),
    (NULL, 'Database administration', '\mdba\M|database\s+administrat|backup\s+(and|&)\s+recovery', 'Database'),
    (NULL, 'Product discovery', 'product\s+discovery|user\s+stor(y|ies)|product\s+roadmaps?', 'Product'),
    (NULL, 'PMP / PRINCE2', '\mpmp\M|prince\s*2', 'Product'),
    (NULL, 'UiPath / RPA', 'uipath|\mrpa\M|blue\s*prism|automation\s+anywhere', 'ERP & CRM'),
    (NULL, 'ArcGIS / GIS', 'arcgis|\mqgis\M|\mgis\M|geospatial', 'Data');

-- Matched once per job by the match_skills step (a regex scan of every job on every read would
-- slow down as the warehouse grows); the skill is kept as text so the list above can change.
CREATE TABLE IF NOT EXISTS core.job_skill (
    job_id bigint NOT NULL REFERENCES core.job ON DELETE CASCADE,
    skill  text   NOT NULL,
    PRIMARY KEY (job_id, skill)
);

-- The embedding of each job's text and of each CV (embed.py, a local Ollama model), one row per job or CV,
-- kept as plain real[] so no extension is needed; they go with their job or CV.
CREATE TABLE IF NOT EXISTS core.job_vector (
    job_id bigint PRIMARY KEY REFERENCES core.job ON DELETE CASCADE,
    vector real[] NOT NULL,
    model  text   NOT NULL
);
CREATE TABLE IF NOT EXISTS core.cv_vector (
    label       text PRIMARY KEY REFERENCES core.cv ON DELETE CASCADE,
    vector      real[] NOT NULL,
    model       text   NOT NULL,
    embedded_at timestamptz  -- embed.py embeds a CV again only when it was uploaded after this (empty: embed it)
);
-- Each sentence of your CVs' experience and projects, once however many CVs have it, and its vector (embed.py cv_lines).
CREATE TABLE IF NOT EXISTS core.cv_line (
    line   text   PRIMARY KEY,
    vector real[] NOT NULL,
    model  text   NOT NULL
);

-- Claude's grade of a job (grade.py, run by the job_radar DAG after embed): the tracker and the
-- emails read it through mart.job_status; goes with its job. restriction: what keeps someone living in
-- Cairo from taking the job (a citizenship, a work permit), empty when nothing does.
CREATE TABLE IF NOT EXISTS core.claude_grade (
    job_id    bigint PRIMARY KEY REFERENCES core.job ON DELETE CASCADE,
    score     smallint    NOT NULL,  -- 0-10, 10 = apply today
    cv        text        NOT NULL,  -- the label of the CV to send (core.cv)
    reason    text        NOT NULL,
    model     text        NOT NULL,
    graded_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE core.claude_grade ADD COLUMN IF NOT EXISTS restriction text;

-- A slim record of every job ever stored, for the market trends: kept when transform deletes the job after KEEP_DAYS.
-- No description and nothing about you: the key, when it was first and last seen, its board, role, place, Claude's
-- score and the day its page was found closed (describe). transform writes it before it deletes; a second run the same
-- day changes nothing.
CREATE TABLE IF NOT EXISTS core.job_history (
    job_key    text PRIMARY KEY,
    first_seen date NOT NULL,
    last_seen  date NOT NULL,
    source     text NOT NULL,
    role       text NOT NULL,
    place      text NOT NULL,
    score      smallint,
    closed_on  date
);

-- How many postings each board gave a day and how many load dropped as not for Egypt (steps.drop_warning): a board
-- that suddenly drops all, or none where it used to drop half, gets a WARNING in the run log.
CREATE TABLE IF NOT EXISTS core.extract_count (
    day      date    NOT NULL,
    board    text    NOT NULL,
    postings integer NOT NULL,
    dropped  integer NOT NULL,
    PRIMARY KEY (day, board)
);

-- Your own deadlines (a coding test, an interview, a document to send), edited on the tracker's Today page: about a job
-- in the radar (job_id) or free text (what), with the day it is due and a note.
CREATE TABLE IF NOT EXISTS core.deadline (
    deadline_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id      bigint REFERENCES core.job ON DELETE SET NULL,
    what        text    NOT NULL,
    due         date    NOT NULL,
    note        text,
    done        boolean NOT NULL DEFAULT false
);

-- Views hold no data: dropped and created again on every run, so a change here always applies.
DROP VIEW IF EXISTS mart.job_status, mart.skill_demand, mart.jobs_daily, mart.job_skill, mart.startup;

-- Every job with how many of your skills (the ones with a track: the market's other skills are left out here) it asks for, how much of that your best CV shows, Claude's score,
-- and your status ('new' until you set one). restriction is the phrase that bars someone living in Egypt (the regex's,
-- else Claude's). Whether a job is hidden is worked out at run time from the score, the restriction, the status and
-- settings.yaml features (config.hidden_reason), in the email and the tracker, never here.
CREATE VIEW mart.job_status AS
SELECT j.job_id, j.role_rank, j.role, j.title, j.company, j.place, j.location, j.source, j.job_url,
       j.target_company, j.company_size, coalesce(k.skill_matches, 0) AS skill_matches, k.skills_matched,
       j.description IS NOT NULL AS described, j.date_posted, j.first_seen, j.emailed_at,
       coalesce(a.status, 'new') AS status, a.note, a.updated_at, v.cv_label, v.cv_coverage, j.posted_at,
       -- apply within 48 hours of the posting: 1 within 12 hours, 2 within 24, 3 over 1 day, 4 over 2 days,
       -- 5 over 3 days; 6 deleted by tomorrow's transform (first seen 4 days ago, config.KEEP_DAYS, no status set)
       CASE WHEN a.job_id IS NULL AND j.first_seen < current_date - 3 THEN 6
            WHEN now() - j.posted_at <= interval '12 hours' THEN 1 WHEN now() - j.posted_at <= interval '24 hours' THEN 2
            WHEN now() - j.posted_at <= interval '48 hours' THEN 3 WHEN now() - j.posted_at <= interval '72 hours' THEN 4
            ELSE 5 END AS fresh_level,
       g.score, g.reason AS grade_reason, g.cv AS grade_cv, coalesce(j.restriction, g.restriction) AS restriction,
       j.cv_similarity, j.years_required, v.skills_on_cv, v.skills_missing, j.cv_evidence
FROM core.job j
LEFT JOIN (SELECT job_id, count(*) AS skill_matches, string_agg(skill, ', ' ORDER BY skill) AS skills_matched
           FROM core.job_skill JOIN core.skill USING (skill) WHERE track IS NOT NULL GROUP BY job_id) k USING (job_id)
-- the best CV for the job (highest coverage) and which of the job's skills that CV shows and does not show
LEFT JOIN (SELECT DISTINCT ON (job_id) job_id, label AS cv_label, cv_coverage, skills_on_cv, skills_missing
           FROM (SELECT k.job_id, c.label, round(100.0 * count(cs.skill) / count(*)) AS cv_coverage,
                        string_agg(k.skill, ', ' ORDER BY k.skill) FILTER (WHERE cs.skill IS NOT NULL) AS skills_on_cv,
                        string_agg(k.skill, ', ' ORDER BY k.skill) FILTER (WHERE cs.skill IS NULL) AS skills_missing
                 FROM core.job_skill k JOIN core.skill ks ON ks.skill = k.skill AND ks.track IS NOT NULL CROSS JOIN core.cv c
                 LEFT JOIN core.cv_skill cs ON cs.label = c.label AND cs.skill = k.skill
                 GROUP BY k.job_id, c.label) per_cv
           ORDER BY job_id, cv_coverage DESC) v USING (job_id)
LEFT JOIN core.claude_grade g USING (job_id)
LEFT JOIN core.application a USING (job_id)
WHERE j.in_scope;

-- How many of the last 90 days' described jobs of each role ask for each of your skills.
CREATE VIEW mart.skill_demand AS
WITH described AS (
    SELECT job_id, role_rank, role FROM core.job
    WHERE description IS NOT NULL AND first_seen >= current_date - 90 AND in_scope
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
WHERE in_scope
GROUP BY first_seen, place, role_rank, role, source;

-- The startup directory with each startup's open jobs (any job in core.job, yours or the website's, at a company of the
-- same name: its letters and digits), newest first, at most 50: what the website publishes as jobs.startup.
CREATE VIEW mart.startup AS
WITH open AS (
    SELECT regexp_replace(lower(company), '[^a-z0-9]', '', 'g') AS name_key,
           (array_agg(title ORDER BY first_seen DESC, job_id))[1:50] AS open_roles
    FROM core.job GROUP BY 1
)
SELECT s.*, coalesce(o.open_roles, '{}') AS open_roles
FROM core.startup s
LEFT JOIN open o ON o.name_key = regexp_replace(lower(s.company), '[^a-z0-9]', '', 'g');

-- Your applications as your mailboxes tell them (applications.py, read-only): one row per email, kept
-- after its job is deleted, so the history stays. kind 'other' is an email read and found to be no
-- application news, kept so it is never read again. job_id: the job in core.job it is about, when found.
CREATE TABLE IF NOT EXISTS core.application_email (
    message_id  text PRIMARY KEY,
    received_at timestamptz NOT NULL,
    sender      text        NOT NULL,
    subject     text        NOT NULL,
    company     text,
    title       text,
    kind        text        NOT NULL CHECK (kind IN ('applied', 'viewed', 'rejected', 'interview', 'offer', 'hired', 'other')),
    job_id      bigint REFERENCES core.job ON DELETE SET NULL
);
-- the Gmail address whose All Mail the email was read from (MAILBOXES in .env): follow-ups and interview prep read its
-- text from that mailbox again. Empty only for a row made before the column existed, until it is set once by hand.
ALTER TABLE core.application_email ADD COLUMN IF NOT EXISTS mailbox text;
-- 'hired' (an offer signed or accepted, a welcome aboard): a warehouse made before it gets the wider check here, once
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'core.application_email'::regclass
                   AND conname = 'application_email_kind_check' AND pg_get_constraintdef(oid) LIKE '%''hired''%') THEN
        ALTER TABLE core.application_email DROP CONSTRAINT IF EXISTS application_email_kind_check;
        ALTER TABLE core.application_email ADD CONSTRAINT application_email_kind_check
            CHECK (kind IN ('applied', 'viewed', 'rejected', 'interview', 'offer', 'hired', 'other'));
    END IF;
END $$;

-- The day you started at a company that hired you, set by hand (your own fact: no email states it reliably); the
-- tracker's Applications table shows it as Started, matching the company by its letters and digits alone.
CREATE TABLE IF NOT EXISTS core.hire (
    company    text PRIMARY KEY,
    started_on date NOT NULL
);

-- The emails extract_email read and found no job in (newsletters, a recruiter's hello): their Message-IDs, so the next
-- run does not download them again. An email with jobs is remembered by its raw.job_posting rows. Kept 7 days.
CREATE TABLE IF NOT EXISTS core.email_seen (
    message_id text        PRIMARY KEY,
    seen_at    timestamptz NOT NULL DEFAULT now()
);

-- The run log (runlog.py): one row per step each time python -m job_radar runs it, for the tracker's Last run tab.
-- status off: the step ran and said its switch in settings.yaml is off; warn: it printed a WARNING; running: it started
-- and has not ended (a row that stays running was killed at its Airflow timeout; finished_at is empty until it ends).
-- summary: what the step printed that shows trouble (WARNING, skipped, "0 of", "off in settings") and its last line,
-- or the error of a failed step. The token and cost columns hold the step's Claude calls; model and cost_usd are
-- empty when it made none (cost_usd also when a model has no price in runlog.PRICES).
CREATE TABLE IF NOT EXISTS core.step_run (
    step               text        NOT NULL,
    started_at         timestamptz NOT NULL,
    finished_at        timestamptz,
    status             text        NOT NULL CHECK (status IN ('running', 'ok', 'warn', 'failed', 'off')),
    summary            text,
    model              text,
    input_tokens       bigint      NOT NULL DEFAULT 0,
    output_tokens      bigint      NOT NULL DEFAULT 0,
    cache_write_tokens bigint      NOT NULL DEFAULT 0,
    cache_read_tokens  bigint      NOT NULL DEFAULT 0,
    cost_usd           numeric,  -- exact dollars at runlog.PRICES
    PRIMARY KEY (step, started_at)
);
