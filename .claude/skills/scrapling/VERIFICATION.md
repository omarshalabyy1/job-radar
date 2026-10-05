# Verification matrix

Last evidence review: **2026-09-28**.
Pinned upstream version: **Scrapling 0.4.15**.

| Claim | Status | Evidence / boundary |
|---|---|---|
| Scrapling 0.4.15 is the pinned compatibility target | VERIFIED | Upstream GitHub release `v0.4.15`, published 2026-08-23; `UPSTREAM_VERSION` pins the repository target. |
| `Response.markdown()` generates LLM-ready markdown with prompt injection stripping | VERIFIED_IN_CI when green | Upstream release notes, signature introspection in `scripts/verify_upstream.py`, and runtime response tests. |
| `SiteToMarkdownSpider` crawls full websites into markdown corpora | VERIFIED_IN_CI when green | Imported from `scrapling.spiders` in `scripts/verify_upstream.py`. |
| Browser sessions reuse tabs and expose `close_pages()` | VERIFIED_IN_CI when green | DynamicSession/StealthySession attribute introspection in `scripts/verify_upstream.py`. |
| `ProxyRotator` is importable from `scrapling.fetchers` | VERIFIED_IN_CI when green | Current upstream API docs plus `scripts/verify_upstream.py`. |
| `FetcherSession` exposes `proxy_rotator` and safe redirect controls | VERIFIED_IN_CI when green | Current upstream API docs plus signature introspection. |
| Spider exposes `robots_txt_obey` | VERIFIED_IN_CI when green | Current upstream spider docs plus attribute introspection. |
| `scrapling mcp` requires `--auth-token` for HTTP transport by default | VERIFIED | Upstream MCP documentation and breaking changes section in v0.4.15; this repository does not claim a runtime proof of the HTTP auth path. |
| Pinned Scrapling MCP stdio initializes through MCP Inspector 2.8.0 and exposes the documented core tools | VERIFIED_IN_CI when MCP black-box job is green | `.github/workflows/mcp-inspector.yml` runs Inspector `tools/list`; `scripts/assert_mcp_inspector_tools.py` checks `make_request`, `open_request_session`, and `session_fetch`. No browser/site/credential behavior is implied. |
| Every browser/anti-bot flow works on every target site | NOT_PROVEN | Target-site behavior, browser dependencies, network policy and authorization vary. |
| Adaptive selector relocation is semantically correct for critical data | NOT_PROVEN by relocation alone | Requires domain validation after match. |
| Scraped web content is safe to pass directly to an autonomous agent | REJECTED | Treat web content as untrusted input; prompt injection and SSRF must be considered. |

## Authoritative upstream references

- Releases: `https://github.com/D4Vinci/Scrapling/releases`
- Documentation: `https://scrapling.readthedocs.io/en/latest/`
- Building RAG systems: `https://scrapling.readthedocs.io/en/latest/ai/building-rag-systems.html`
- Proxy rotation: `https://scrapling.readthedocs.io/en/latest/api-reference/proxy-rotation.html`
- Spider robots behavior: `https://scrapling.readthedocs.io/en/latest/spiders/getting-started.html`
- MCP server breaking changes: `https://scrapling.readthedocs.io/en/latest/ai/mcp-server.html#breaking-changes`
- MCP Inspector 2.8.0 release: `https://github.com/modelcontextprotocol/inspector/releases/tag/2.8.0`

## Re-verification triggers

Compatibility returns to `NOT_PROVEN` until checked when:

- PyPI/GitHub latest release differs from `UPSTREAM_VERSION`;
- documented import paths or signatures change;
- Spider lifecycle or adaptive-selector behavior changes;
- MCP transport/CLI flags change;
- security-relevant defaults such as redirect handling change;
- the pinned MCP Inspector version or MCP protocol-era behavior changes.

## Evidence vocabulary

- `VERIFIED` — authoritative upstream evidence directly supports the scoped claim.
- `VERIFIED_IN_CI` — a deterministic repository check also verifies the installed API surface.
- `PARTIAL` — some evidence exists, but a material runtime property remains untested.
- `NOT_PROVEN` — do not state the claim as fact for the target environment.
- `REJECTED` — claim is intentionally not supported because it is unsafe or too broad.
