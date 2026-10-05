# scrapling-skill

Version-aware Agent Skill for [Scrapling](https://github.com/D4Vinci/Scrapling), focused on adaptive web extraction, RAG Markdown generation, fetcher/session selection, spiders, proxy rotation, robots-aware crawling and MCP integration.

## Current compatibility target

**Scrapling 0.4.15** — last repository evidence review: **2026-09-04**.

The compatibility target is machine-readable in `UPSTREAM_VERSION`. A scheduled GitHub Actions check detects when PyPI publishes a different latest version so the repository cannot silently stay “verified” forever.

## Quality model

This repository separates four kinds of proof:

- **Documentation verification** — current upstream docs/release are referenced in `VERIFICATION.md`.
- **Static validation** — skill frontmatter, local links, eval JSONL, fenced Python syntax and obvious secret patterns.
- **API-surface verification** — CI installs the pinned Scrapling version and introspects the imports/signatures used by this skill.
- **MCP black-box verification** — a separate least-privilege CI job uses MCP Inspector 2.8.0 over local stdio to initialize the pinned Scrapling MCP server and verify its documented tool surface.
- **Runtime/browser behavior** — remains `NOT_PROVEN` unless a test actually launches the relevant browser/network flow in an authorized environment.

A successful import is not proof that every target website, anti-bot flow or browser environment will work.

## Repository layout

| Path | Purpose |
|---|---|
| `SKILL.md` | concise agent-facing decision workflow (RAG, fetchers, MCP, safety) |
| `references/patterns.md` | implementation patterns for the pinned upstream line |
| `evals/cases.jsonl` | behavioral evaluation cases |
| `scripts/verify_upstream.py` | installed API compatibility checks |
| `scripts/check_upstream_drift.py` | detects a newer PyPI release |
| `VERIFICATION.md` | evidence matrix and re-verification triggers |
| `SECURITY.md` | SSRF, prompt-injection, secret and disclosure rules |

The previous long-form skill and patterns are preserved in versioned `*.pre-2026-08-15.md` references.

## Install for local validation

```bash
python -m pip install -r requirements-dev.txt
python scripts/validate_markdown.py
python scripts/validate_skill.py
python scripts/verify_upstream.py
```

Browser binaries are deliberately not required for the default CI compatibility check. Run `scrapling install` only in an environment where browser-backed tests are intentionally being exercised.

## Use as an Agent Skill

Clone the repository and copy `SKILL.md` plus the referenced files into the skill directory used by your agent environment.

```bash
git clone https://github.com/Thanane15M/scrapling-skill.git
```

The repository does not assume one proprietary agent runtime.

## Safety

Use scraping only where you are authorized. Prefer official APIs when available, respect applicable law/privacy/site rules, and treat retrieved web content as untrusted input that may contain prompt injection.

## License

MIT for this repository. Scrapling itself is an upstream project with its own license and maintainers.
