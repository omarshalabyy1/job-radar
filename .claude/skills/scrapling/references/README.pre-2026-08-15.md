![Claude Skill](https://img.shields.io/badge/Claude-Skill-orange) ![Scrapling](https://img.shields.io/badge/Scrapling-v0.4.9-blue) ![MIT License](https://img.shields.io/badge/License-MIT-green)

# scrapling-skill

A Claude Skill for [Scrapling](https://github.com/D4Vinci/Scrapling) — an adaptive web-scraping framework that goes from a single request to a full-scale async crawl.

BeautifulSoup4 is roughly 780× slower than Scrapling on the official parsing benchmark, yet most Python scraping tutorials still default to it. The official Scrapling docs cover the API surface. **This skill covers what to actually do with it** — and is verified against the real `v0.4.9` API, not from memory.

## Why this skill exists

Three high-value capabilities aren't obvious from the README:

- **Self-healing selectors** — a *two-phase* workflow (`auto_save` then `adaptive`), not a magic flag.
- **Fetcher selection** — knowing when to use `Fetcher` vs `StealthyFetcher` vs `DynamicFetcher` (and their Session/Async variants) means reading several doc pages. This skill ships a one-glance decision tree.
- **MCP + spiders** — the native MCP server, multi-session spiders, lifecycle hooks (`on_scraped_item`, `is_blocked`, `retry_blocked_request`), and pause/resume crawling, with copy-paste-correct invocations.

## What's covered

- Fetcher selection decision tree (HTTP / stealth / dynamic × one-shot / session / async)
- Self-healing selectors with the correct two-phase `auto_save` → `adaptive` workflow
- Spider architecture — concurrency, download delays, real lifecycle hooks, multi-session routing
- Correct `ProxyRotator` usage (`proxy_rotator=`, not `proxy=rotator.next()`)
- Native MCP server wiring (`scrapling mcp`, stdio + HTTP transports)
- CLI usage for one-off scraping without writing a script
- BeautifulSoup → Scrapling migration cheat sheet
- A **gotchas checklist** (the `scrapling install` prerequisite, the v0.4.9 proxy-leak fix, etc.)

## Install

```bash
# As a Claude Skill
claude skill install https://github.com/Thanane15M/scrapling-skill
# or copy SKILL.md into .claude/skills/scrapling/

# The underlying library (pin the version)
pip install "scrapling[fetchers]==0.4.9"
scrapling install   # required before any browser-based fetcher
```

## Versioning

This skill targets **Scrapling v0.4.x** (verified on `0.4.9`). The 0.4 line introduced breaking API changes (async spiders, `ProxyRotator`). If you're on 0.3.x, upgrade before using these patterns.

## A note on the upstream project

Scrapling is authored by Karim Shoair (D4Vinci) and licensed **BSD-3-Clause**. It's intended for educational and research use — respect each target site's `robots.txt`, terms of service, and applicable data-protection law (e.g. GDPR). This skill repository is MIT-licensed; the Scrapling library is not.

## License

MIT
