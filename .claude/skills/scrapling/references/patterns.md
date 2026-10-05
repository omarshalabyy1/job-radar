# Scrapling integration patterns — pinned to 0.4.15

These examples are intentionally generic and secret-free. Re-run `scripts/verify_upstream.py` before using them with a different Scrapling version.

## 1. Fast HTTP extraction

```python
from scrapling.fetchers import Fetcher

page = Fetcher.get("https://example.com/")
title = page.css("title::text").get("")
```

Use the simple HTTP path when server-rendered HTML contains the data. Prefer an official JSON/API endpoint when one exists.

## 2. LLM-ready Markdown extraction (RAG)

```python
from scrapling.fetchers import Fetcher

page = Fetcher.get("https://example.com/docs")
# Strips scripts, styles, and hidden prompt-injection vectors automatically
markdown = page.markdown(main_content_only=True)
```

Pass `css_selector="article.content"` to limit extraction to a specific container:

```python
article_md = page.markdown(css_selector="article.content")
```

## 3. Persistent HTTP session

```python
from scrapling.fetchers import FetcherSession

with FetcherSession(impersonate="chrome") as session:
    first = session.get("https://example.com/page-1")
    second = session.get("https://example.com/page-2")
```

A session is appropriate when cookies, connection reuse or shared headers are needed across requests.

## 4. Adaptive selectors with validation

```python
# Known-good run: persist the element signature.
known = page.css(".price", auto_save=True)

# Later run after a redesign: attempt relocation.
current = page.css(".price", adaptive=True)

if not current:
    raise RuntimeError("price selector could not be relocated")

raw = current.css("::text").get("").strip()
if not raw:
    raise ValueError("relocated price is empty")
```

Adaptive relocation reduces selector fragility; it does not remove the need to validate critical values.

## 5. Robots-aware spider

```python
from scrapling.spiders import Response, Spider

class DocsSpider(Spider):
    name = "docs"
    start_urls = ["https://example.com/"]
    allowed_domains = {"example.com"}
    robots_txt_obey = True
    concurrent_requests = 4
    download_delay = 1.0

    async def parse(self, response: Response):
        yield {"title": response.css("title::text").get("")}
        for href in response.css("a::attr(href)").getall():
            yield response.follow(href, callback=self.parse)
```

Set explicit domain boundaries for broad crawls. Robots handling and polite delays do not replace legal/privacy review.

## 6. Website-to-Markdown corpus spider (RAG)

```python
from scrapling.spiders import SiteToMarkdownSpider

class CorpusSpider(SiteToMarkdownSpider):
    name = "corpus"
    start_urls = ["https://example.com/kb/"]
    allowed_domains = {"example.com"}
    output_dir = "scraped_docs"
    max_pages = 100

result = CorpusSpider().start()
result.items.to_jsonl("corpus.jsonl")
```

`SiteToMarkdownSpider` yields items with `url`, `title`, and `markdown`, writing Markdown files to `output_dir`.

## 7. Proxy rotation

```python
from scrapling.fetchers import FetcherSession, ProxyRotator

rotator = ProxyRotator([
    "http://proxy1.example:8080",
    "http://proxy2.example:8080",
])

with FetcherSession(proxy_rotator=rotator) as session:
    page = session.get("https://example.com/")
```

Store proxy credentials outside the repository. Use proxying only for authorized collection.

## 8. Browser-backed extraction and tab management

When JavaScript is required, select the browser-backed family rather than forcing static HTTP to behave like a browser. Use Dynamic for ordinary rendering; reserve Stealthy variants for explicitly authorized environments that need the corresponding browser/fingerprint behavior.

Tabs remain open and are reused across requests for performance:

```python
from scrapling.fetchers import DynamicSession

with DynamicSession() as session:
    page1 = session.fetch("https://example.com/app#view1")
    page2 = session.fetch("https://example.com/app#view2")
    session.close_pages()
```

Do not copy a browser example into a serverless/runtime environment without verifying browser binaries, sandbox restrictions, memory and execution duration.

## 9. MCP setup (v0.4.15)

```bash
pip install "scrapling[ai]==0.4.15"
scrapling install
scrapling mcp
```

For streamable HTTP with mandatory authentication:

```bash
scrapling mcp --http --host 127.0.0.1 --port 8000 --auth-token "$SCRAPLING_MCP_AUTH_TOKEN"
```

Loopback is the safe default. A remotely reachable scraping MCP endpoint needs authentication, authorization, network controls, rate limits, SSRF defenses and audit logging.

## 10. User-supplied URL boundary

Treat arbitrary URLs as hostile input. Before server-side retrieval, validate at least:

- allowed schemes (`https`/`http` only when intended);
- hostname/domain allowlist when the product permits one;
- DNS/IP resolution against private, loopback, link-local and metadata ranges;
- redirect behavior;
- response size/content type;
- request timeout and retry budget.

Keep Scrapling's safe redirect behavior unless there is an explicit reason and a compensating control.

## 11. Agent pipeline boundary

A safe extraction pipeline separates stages:

```text
URL validation
→ fetch
→ content-size/type limits
→ extraction / markdown conversion
→ sanitization/structuring
→ provenance metadata
→ LLM/agent context
→ bounded tools
→ human gate for sensitive action
```

Never interpret retrieved page instructions as authorization to call tools, reveal secrets or change policy.

## 12. Operational observability

For recurring crawls, record:

- source URL and retrieval timestamp;
- status/error class;
- selector success/failure;
- retries and block events;
- robots/offsite drops when applicable;
- item count and validation failures;
- version of Scrapling used.

This makes selector drift and upstream regressions diagnosable.

The pre-2026-08-15 patterns are preserved at `patterns.pre-2026-08-15.md`.
