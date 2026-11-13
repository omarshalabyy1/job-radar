# Tracker Overview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An Overview page (the tracker's home) with the application counts and one applications table per company.

**Architecture:** A pure helper `tracker/overview.py` turns warehouse rows into one dict per company (stage, board,
dates, latest news) and counts them; the Streamlit page `tracker/app_pages/overview.py` queries, calls it, draws it.
The Applications and Today pages drop the sections the Overview replaces.

**Tech Stack:** Python 3, Streamlit (`st.metric`, `st.segmented_control`, `st.dataframe`), Postgres warehouse, pytest.

**Spec:** `docs/superpowers/specs/2026-11-13-tracker-overview-design.md`

## Global Constraints
- No schema change; never DROP/TRUNCATE; Neon untouched; `tracker.decide` unchanged.
- `tests/` is locked: add `tests/test_overview.py`, edit no existing test.
- Keep names other code and tests import: `STAGE_EMAILS`, `STAGE_LABELS`, `follow_ups`, `APPLICATIONS`,
  `COMPANIES_BY_KIND`, `GHOSTED_DAYS`; keep page file `app_pages/applications.py` (its test reads "Follow-up replies"
  and the last metric "Answered after a follow-up"); the insights page's first metric stays the interview rate.
- Stage order: waiting, viewed, assessment, interview, rejected, offer. Range: 30 / 90 / All days by latest activity.

## Review Focus
- An email naming no company: grouped by its title, as the funnel does.
- An interview followed by a later rejection: Rejected; a rejection followed by a new interview: Interview.
- A company with only your status (no email): counted, board from the job source.
- `core.job.source` values `email`, `manual`, `manual:*`, `apify:linkedin_remote`: email/manual fall through to the
  email channel; `apify:linkedin*` reads LinkedIn.
- No applications in range: the page shows zeros and an empty-state line, no exception.

---

### Task 1: `tracker/overview.py` helper

**Files:** Create `tracker/overview.py`; Test `tests/test_overview.py`

**Interfaces (produces):**
- `STAGES = ["waiting", "viewed", "assessment", "interview", "rejected", "offer"]`
- `email_kind(kind: str, subject: str | None, action: str | None) -> str` — "interview" becomes "assessment" when
  subject or action matches `assessment|\btest\b|exam|hackerrank|codility|video application|\btask\b|challenge` (case-insensitive).
- `board(attempt_board: str | None, source: str | None, sender: str | None) -> str` — attempt board unless None/"other";
  else source: contains linkedin/indeed/wuzzuf/ashby/workable/naukrigulf/bayt/glassdoor -> that name capitalised
  (LinkedIn, Indeed, Wuzzuf, Ashby, Workable, Naukrigulf, Bayt, Glassdoor); source `email`, `manual*` or None -> 
  `applications.channel(sender)` if sender else ""; any other source as is.
- `applications(mail: list[tuple], statuses: list[tuple]) -> list[dict]` — mail rows
  `(company, title, kind, received_at, subject, action, sender)`; status rows
  `(company, title, status, updated_at, source, attempt_board, job_url)`. One dict per company key
  (`name_key(company)` or lowercased title) that is an application (spec): keys `company, job, board, stage,
  applied_on, latest_at, latest_news, latest_email, job_url`.
- `in_range(apps: list[dict], days: int | None, now: datetime) -> list[dict]`
- `counts(apps: list[dict]) -> dict[str, int]` — every stage plus `applied` and `responses`.

- [ ] Step 1: tests: `test_email_kind_splits_assessments`, `test_board_precedence` (attempt "linkedin" wins; "other" +
  source "apify:linkedin_remote" -> "LinkedIn"; source "email" + LinkedIn sender -> "LinkedIn"; nothing -> ""),
  `test_rejection_after_interview_is_rejected`, `test_interview_after_rejection_is_interview`,
  `test_status_only_company_counts`, `test_email_without_company_groups_by_title`, `test_viewed_only_is_not_an_application`,
  `test_counts_add_up` (sum of stages == applied; responses == applied - waiting), `test_in_range_by_latest_activity`.
- [ ] Step 2: run `pytest tests/test_overview.py` -> fails (no module).
- [ ] Step 3: implement.
- [ ] Step 4: run -> passes. Commit.

### Task 2: Overview page and navigation

**Files:** Create `tracker/app_pages/overview.py`; Modify `tracker/app.py`, `tracker/overview.py` (SQL constants);
Test `tests/test_overview.py`

- SQL in `tracker/overview.py`: `MAIL` (application_email, kind <> 'other', company from the matched job else the
  email's, subject, `core.mail.action` joined on message_id, sender); `STATUSES` (core.application applied / interview /
  offer / rejected joined to core.job, latest apply_attempt board).
- Page: title "Overview"; range `st.segmented_control` (Last 30 days / 90 days / All, default 30); metrics row 1 Can apply
  (192-style count of `load_jobs()` new/saved with no hidden_reason, delta-free help naming the 48h count), Applied,
  Responses; row 2 one metric per stage; `st.segmented_control` Show (All + stages) filters a `st.dataframe` sorted
  interview, assessment first then latest_at desc.
- `app.py`: Overview first in "Your search", `default=True`; Today no longer default; Applications page titled
  "Follow-ups".
- [ ] Step 1: test `test_overview_page_renders` (AppTest on `app_pages/overview.py`: no exception, a metric labelled
  "Applied"). Step 2 fail, Step 3 implement, Step 4 pass, commit.

### Task 3: Trim Applications and Today, move funnel and company intelligence

**Files:** Modify `tracker/applications_tab.py`, `tracker/app_pages/applications.py`, `tracker/app_pages/today.py`,
`tracker/app_pages/insights.py`

- applications_tab: `render` keeps Follow up, Follow-up replies, Drafts; drops Applied by you, the seven counters (keep
  the "no emails yet" info), the Applications table, Funnel, Company intelligence. New `render_funnel(query)` and
  `render_companies(query)` hold the two moved sections unchanged; insights page calls them after `render()`.
- today: drop section 3 "Your applications" and imports it alone used.
- [ ] Run `pytest tests/test_insights.py tests/test_applications.py tests/test_overview.py` -> pass. Commit.

### Task 4: Verify and close
- [ ] Second tracker from this worktree on 127.0.0.1:8502 (image `job-radar:3.3.2`, worktree mounted, `--env-file` the
  main checkout's `.env`, network of the compose project, `WAREHOUSE_HOST=warehouse`); check Overview, Follow-ups,
  Today, Interview rate in the browser; stop it.
- [ ] STATE.md log line; `bash check.sh` -> `GATE: PASS`; commit.
