---
name: scraping-orchestrator
description: Entry point for ANY scraping job (scrape, crawl, extract listings, prices, hidden API, 403/429, blocked, Cloudflare, CAPTCHA, login wall, cookies, "سكرب", "اسحب البيانات") and any project that scrapes. Builds with requests, curl_cffi and Playwright, and routes each step to the skill or file it needs (scraping, web-scraping, playwright-skill); scrapling, crawl4ai and Firecrawl are measured fallbacks only. Use it first, before any of them.
---

> **System skills (MUST):** your next action after reading this skill is a Skill tool call to `superpowers:using-superpowers`, then the matching superpowers skill (brainstorming before new work, systematic-debugging for a bug or failure, test-driven-development for code, verification-before-completion before saying done). If the task reaches outside this skill's domain, invoke `skill-finder` to pick the skill that owns it. CLAUDE.md rules win on conflict.

# Scraping orchestrator

Route each step to the one skill or file it needs (skills in `~/.claude/skills/<name>/`; where a row names a file, `Read` only that file). Stop at the first step that gets the data.

**Tools, in order:** `requests` → `curl_cffi` (`impersonate="chrome"`) → Playwright. Scrapling, crawl4ai and Firecrawl only when Playwright is measured blocked or too slow, recorded in the site notes.

## 1. Start from what is known

Read `~/.scraping-profiles/<site>/notes.md` if it exists (rung that worked, rate limit, selectors, the site's own item count, each dated). Start at that rung and re-check with one request; old notes are a hypothesis. After every job, update it in a few lines (create it if missing).

## 2. Route

| Step | Load / read | Use it for |
|---|---|---|
| Plan | Skill `scraping` | Size the job (one page = ten-line script, recurring = Airflow task) and what "complete" means. Always first. |
| Probe | `scraping/scripts/probe.py <url> --out probe_out` | A dozen requests: data in HTML or embedded JSON, JS shell, who is in front, sitemaps, WordPress totals. |
| Hidden API | Built-in browser `read_network_requests`; `web-scraping/strategies/api-discovery.md`, `traffic-interception.md` | JS shell: find the JSON call and call it with `requests` / `curl_cffi`. Best path when it exists. |
| Sitemap or list | `web-scraping/strategies/sitemap-discovery.md`, `scraping/reference/recipes.md` | URL list from the sitemap, CMS API or `__NEXT_DATA__`. |
| HTML pages | `requests`, then `curl_cffi` on 401 / 403 / timeout; BeautifulSoup | Default for pages. `requests` first: some sites block `curl_cffi` but not `requests`. |
| JS pages, clicks, forms | Playwright, headless Chromium (skill `playwright-skill`) | `domcontentloaded`, then `networkidle` capped at ~10 s; clicks, filters, infinite scroll with no API behind. |
| YouTube video, captions, transcript | Skill `youtube-transcript` (yt-dlp) | Subtitles first, auto-captions next, Whisper only after Omar confirms. Skip its sudo/brew install lines; on Windows use `pip install yt-dlp`. |
| Reddit posts, comments, subreddits | Skill `reddit-fetch` (Playwright MCP) | curl gets 403; the DuckDuckGo hop sets the cookie, then `.json` URLs work. Sequential, 2-3 s apart. |
| X, Instagram, Facebook, LinkedIn, TikTok | Skill `apify:apify-ultimate-scraper` (Apify budget), or `brightdata-plugin:data-feeds` (`bdata pipelines`, 1 credit per record) | Both need no platform login. For a logged-in view use Claude in Chrome or `unlock.py` (ladder rung 5). No vetted free skill exists for these four. |
| Blocked | Force ladder below; `scraping/reference/transport.md`, `web-scraping/strategies/anti-blocking.md` | Never stop at the first refusal. |
| Check | `scraping/reference/validation.md` | Counts at every stage against the site's own total, empty fields, duplicates. Before anything is reported. |

Prefer API, then sitemap or list, then pages; move to a heavier tool only when the lighter one fails on a measurement.

| Signal (probe, response or browser) | Go to |
|---|---|
| `__NEXT_DATA__`, `window.__INITIAL_STATE__`, `application/ld+json` | Parse the embedded JSON, skip the HTML |
| Near-empty shell, data only after JavaScript | Hidden API |
| WordPress (`/wp-json/`, `x-wp-total`) | REST API with `per_page=100` |
| Data in the HTML, 200 | HTML pages |
| `cf-mitigated: challenge`, "Just a moment", 403 from Cloudflare / Akamai / DataDome | Ladder rung 3 (a JS check, not a CAPTCHA) |
| 429 or `Retry-After` | Wait it out, drop concurrency one level, resume |
| Login or cookie wall | Ladder rung 5 |
| 200 but zero items, or a small round count | Wrong page or soft block: compare with the visible page |

### MCP tools by step

Use them for discovery and one-off pulls; production scrapers stay plain Python. A server the session lists as needing authorization (Firecrawl, Nimble, Vercel) works after Omar runs `/mcp` once: say so, use another row meanwhile.

| Step | MCP | Use it for |
|---|---|---|
| Hidden API | Built-in browser (`mcp__Claude_Browser__*`: `read_network_requests`, `read_page`, `javascript_tool`) | See the JSON calls a page makes, then replay them in Python. |
| Signed or obfuscated API | REA (`mcp__rea__*`, skill `reverse-engineer-anything`): `analyze_web_bundle`, `recover_javascript_sources`, `trace_web_module_imports`, `trace_javascript_semantics`, `inspect_web_network_capture`, `observe_web_session` | The JSON call needs a signature, token or encrypted body: read the site's JavaScript, rebuild the logic in Python. |
| App with no website | REA ADB tools (`list_adb_packages`, `pull_adb_package`, `read_adb_logcat`, `inspect_android_class`) | Pull an Android app and find the API it calls. |
| Logged-in sites | Claude in Chrome (`mcp__claude-in-chrome__*`) | Omar's real Chrome with his sessions: read pages and network calls behind a login. |
| JS pages, clicks | Playwright MCP (`mcp__playwright__*`, runs `--isolated`) | Drive a page by hand to find selectors before writing the script. |
| Find sources | `mcp__firecrawl__firecrawl_search`, Exa (`web_search_exa`), Perplexity (`perplexity_search`) | Locate pages, docs and datasets; Firecrawl search also returns matching data providers. |
| Read a page | `firecrawl_scrape`, Exa `web_fetch_exa` | One URL to markdown or JSON when `requests` cannot. |
| Site URLs, sections | `firecrawl_map`, `firecrawl_crawl` | Rung 4 only; counts credits. |
| Structured data | `firecrawl_scrape` with `alexandria`, `firecrawl_find_tools` | A ready data provider (companies, jobs, prices, filings) instead of scraping pages. |
| Prebuilt scrapers | Apify (`search-actors`, `call-actor`, `get-dataset-items`) | An Actor that already scrapes the site; uses Apify budget. |
| Recurring watch | `firecrawl_monitor_*` | Alert on page changes without a DAG. |
| Search, read, drive pages | TinyFish MCP (`search`, `fetch_content`, `run_web_automation`; 2 concurrent) | Hosted browser for a JS or protected page when `requests` and Playwright fail; also `create_dataset` for a table of many entities. |
| Prebuilt job and company data | Bright Data MCP / skills `brightdata-plugin:data-feeds`, `scraper-builder`, `scrape`, `search`, `discover-api` | Structured records (1 credit each) or a scraper built for one site; Bright Data CLI `bdata`. |
| Web data in one call | Skill `nimble:nimble-web-expert` (needs the Nimble CLI) | Extraction templates and search agents for a site. |
| A custom Actor | Skill `apify:apify-actor-development` | Only when no Actor exists and the scrape recurs. |
| Contacts at a company | Apollo MCP (`mcp__ca53a264-*`) | Hiring contacts for a company you scraped; published data only. |

## 3. Plan, then build

Write about ten lines, show Omar (no approval needed, he can redirect), save in the site notes or the project's `docs/`: goal and the site's count to match; pages, fields, selectors or JSON keys; URL source and count; tool and rung with the test numbers; speed (pages/min, total time); one-off or Airflow task; storage (raw kept, then clean table); checks; what will break and the fallback.

Run it: one `requests.Session` or async `curl_cffi` for APIs and pages; one Playwright browser kept open for the whole run with ~8 pages via `asyncio.gather` (reopening per batch halved throughput; more tabs slowed this laptop). Backoff on 429, no fixed delays. Resumable JSONL state (`scraping/scripts/runner_template.py`) only for a few hundred pages or more.

## 4. Force ladder

Read the saved refusal body first (`probe_out/`), then climb one rung at a time:

1. `requests`.
2. `curl_cffi`, `impersonate="chrome"`.
3. Playwright headless; then visible Chromium or `channel="chrome"`.
4. Fallback, only after rung 3 is measured blocked. Test two on the same page (status, items vs the visible page, fields, seconds), pick on the numbers, write the verdict in the notes:
   - Scrapling `StealthyFetcher.fetch(url, headless=True, network_idle=True, solve_cloudflare=False)`, then `real_chrome=True` (skills `scrapling-fetcher` templates, `scrapling`). Jumia 2026-10-05: `curl_cffi` 403, Scrapling 200 with 40/40 prices; Playwright was not tested there.
   - Firecrawl (hosted, costs credits; OAuth login done; MCP `firecrawl` and the `firecrawl` CLI): `scrape <url>` for one JS or protected page, `map <site>` for a site's URLs, `crawl <url>` for a section, `interact` for clicks and scrolling. Count credits in the notes. Never for private people's personal data.
   - crawl4ai with `crawl4ai/references/anti-detection.md` (its stealth lost to Cloudflare on Jumia, 0 items).
   - Keep `solve_cloudflare=False`, also in a copied `stealth_cloudflare.py` (ships `True`). If a Scrapling browser will not launch, run `scrapling install` once.
5. CAPTCHA, login or cookie wall: Omar runs `python ~/.claude/skills/scraping-orchestrator/unlock.py <url> <site>` in his terminal (Claude can start it with `mcp__terminal__run_in_terminal`; the Bash tool has no stdin). He passes the check in the visible window and presses Enter. The profile stays in `~/.scraping-profiles/<site>/` with `session.json` (cookies + user agent): Playwright reuses the profile headless (`launch_persistent_context`), `requests` / `curl_cffi` send those cookies and that user agent. For Airflow, copy `session.json` to `data/cookies/<site>.json` (gitignored, mounted read-only) and fail the task loudly when it expires. Re-run to refresh.

Re-test when it breaks: a refusal, a falling count or empty fields mean the verdict is stale.

## Jobs on request

Omar says "scrape jobs from X", "get jobs now", "find jobs at <company>": do it by hand, in this session, on his Claude plan. Rules of the job-radar repo hold (skill `job-radar`): Egypt and remote jobs only, never open a LinkedIn or Indeed page (Apify actors only; `settings.yaml` apify.boards), never apply, never log in or solve a CAPTCHA.

| The source is | Route |
|---|---|
| A company on an ATS (Greenhouse, Lever, Ashby, Recruitee, SmartRecruiters, Workable) | The ATS's public JSON (`boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true`, `api.lever.co/v0/postings/<token>?mode=json`, `api.ashbyhq.com/posting-api/job-board/<token>`); readers in `job_radar/sources/companies.py` |
| A company's own careers page | Probe, then embedded JSON or the XHR call; else Playwright; `firecrawl_scrape` or TinyFish if blocked |
| A job board (Wuzzuf, Tanqeeb, Bayt, Jooble, Himalayas, Remotive) | The board's reader in `job_radar/sources/`; a new board: hidden API first, then HTML |
| LinkedIn, Indeed, Glassdoor, Wellfound | Apify (`apify.boards`, caps in settings), or `brightdata-plugin:data-feeds` |
| X or Reddit hiring posts | Apify (`x`, `reddit` boards: filtered by `sources/social.py`) |
| Many companies at once | `firecrawl-company-directories`, Exa `web_search_exa`, Apollo for contacts |

Then:

1. Scrape into a list of `{title, job_url, company, location, date_posted, description, searched_for}` (`searched_for`: Egypt or Remote), saved as `E:\job-radar\output\scraped\<site>.json` (the container sees it as `output/scraped/`). Keep only jobs posted in the last 5 days (`KEEP_DAYS`); the age comes from the posting's own date.
2. Store and rank them: `docker exec -w /opt/job-radar job-radar-airflow-1 /opt/job-radar-venv/bin/python -m job_radar.manual_jobs <site> output/scraped/<site>.json`. It stores each posting once as source `manual:<site>`, then runs transform and match_skills.
3. Report: found, kept, new, the paid MCP or Apify cost, and what blocked; write the site notes (step 1). A source that works becomes a reader in `job_radar/sources/` with a saved sample and a test, so the daily run takes it over.

## 5. What to skip in each skill

- `scraping`: "no stealth, stop at a challenge" and polite-pace advice.
- `web-scraping`: the Apify / TypeScript / Actor parts.
- `playwright-skill`: dev-server, responsive and UX-testing parts.
- `scrapling`, `scrapling-fetcher`: robots.txt / ToS preambles, `robots_txt_obey = True`, lower concurrency, added delays.
- `crawl4ai`: "use defuddle / fetch-web" (not installed).
- `firecrawl-*` (28 skills; rung 4 only):
  - Use: `-scrape` (known page), `-crawl` (many pages), `-map` (a site's URLs), `-download` (save a site), `-interact` (clicks, scroll), `-agent` (navigates alone, returns structured data), `-search` (find sources), `-parse` (local PDF, DOCX, XLSX to markdown), `-monitor` (change alerts).
  - Jobs built on them: `-company-directories`, `-lead-gen`, `-knowledge-base`, `-knowledge-ingest`, `-dashboard-reporting`.
  - Also available, skip none: `-seo-audit`, `-qa`, `-competitive-intel`, `-shop`, `-market-research`, `-deep-research`, `-research-papers`, `-research-index`, `-developer-index`, `-demo-walkthrough`, `-lead-research`, `-website-design-clone`, `-workflows`.

## 6. Setup and projects

- Set `PYTHONUTF8=1` (the Windows console dies on Arabic). Not installed: Scrapy, Camoufox; do not route to them.
- A project that scrapes gets a copy: `SKILL.md` and `unlock.py` in `<repo>/.claude/skills/scraping-orchestrator/`, committed; re-copy when this skill changes. Never commit profiles or cookies.
- Scheduled scrapers are Airflow tasks (skill `authoring-dags`); storage in Postgres (`postgresql_de`) or files with `duckdb`.
- Scraped data that ends in a Power BI report: hand the clean tables to skill `powerbi-orchestrator` (Power BI reads clean tables only).

## Omar's rules

- Naming a site is his go-ahead and he takes the risk. Build straight away, stealth included, no robots.txt or terms warnings, and do not ask again.
- robots.txt `Disallow`, "no scraping" terms, anti-bot pages and a 403 never end the job. Never set a robots-obey option; read robots.txt only for sitemap lines.
- Force the scrape: as fast and parallel as the site's rate limit allows. Find the limit (raise concurrency until 429s, step back one level); on a 429 wait out `Retry-After`.
- Python only.
