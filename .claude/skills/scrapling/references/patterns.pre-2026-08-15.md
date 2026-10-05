# Scrapling — Patterns d'intégration réutilisables

Patterns génériques, sans secrets, prêts à adapter. Toute valeur sensible passe par variable
d'environnement (`os.environ`). Cible : Scrapling v0.4.9.

---

## 1. Monitoring de prix avec alerte

```python
import asyncio, os, httpx
from scrapling.fetchers import StealthySession

WEBHOOK_URL = os.environ["ALERT_WEBHOOK_URL"]   # ex. endpoint Telegram/Slack/Discord

async def alert(msg: str):
    async with httpx.AsyncClient() as c:
        await c.post(WEBHOOK_URL, json={"text": msg})

async def monitor_price(url: str, selector: str, threshold: float, interval: int = 3600):
    with StealthySession(headless=True, solve_cloudflare=True) as session:
        # 1er passage : enregistre la signature pour le self-healing
        first = session.fetch(url, network_idle=True)
        first.css(selector, auto_save=True)
        while True:
            page = session.fetch(url, network_idle=True)
            el = page.css(selector, adaptive=True)
            if el:
                raw = el.css('::text').get('').replace('€', '').replace(',', '.').strip()
                try:
                    price = float(raw)
                    if price <= threshold:
                        await alert(f"🔔 Prix cible atteint : {price}€\n{url}")
                except ValueError:
                    pass
            await asyncio.sleep(interval)
```

---

## 2. Crawl multi-pages → PostgreSQL (endpoint FastAPI)

```python
import os
from fastapi import FastAPI
from pydantic import BaseModel
from scrapling.fetchers import Fetcher, StealthyFetcher
import asyncpg

app = FastAPI()
DSN = os.environ["DATABASE_URL"]

class ScrapeReq(BaseModel):
    url: str
    selector: str
    stealth: bool = False
    adaptive: bool = False

@app.post("/scrape/batch")
async def scrape_batch(req: ScrapeReq):
    if req.stealth:
        page = StealthyFetcher.fetch(req.url, block_ads=True)
    else:
        page = Fetcher.get(req.url)

    rows = [
        (req.url, req.selector, el.css('::text').get(''), el.attrib.get("href", ""))
        for el in page.css(req.selector, adaptive=req.adaptive, auto_save=not req.adaptive)
    ]

    conn = await asyncpg.connect(DSN)
    try:
        await conn.executemany(
            "INSERT INTO scraped_data (url, selector, text, href, scraped_at) "
            "VALUES ($1, $2, $3, $4, NOW()) ON CONFLICT DO NOTHING",
            rows,
        )
    finally:
        await conn.close()
    return {"count": len(rows)}
```

Appel (n8n, cron, autre service) : `POST /scrape/batch` avec
`{"url": "...", "selector": ".price", "stealth": true}`.

---

## 3. Spider de veille → pipeline de contenu

```python
import os, httpx
from scrapling.spiders import Spider, Response
from scrapling.fetchers import FetcherSession

INGEST_ENDPOINT = os.environ["CONTENT_INGEST_URL"]

class VeilleSpider(Spider):
    """Scrape des sources d'actualité et pousse chaque article vers un pipeline."""
    name = "veille"
    start_urls = [
        "https://www.batiactu.com/",
        # ajouter vos sources
    ]
    concurrent_requests = 3
    download_delay = 2.0          # politesse

    def configure_sessions(self, manager):
        manager.add("http", FetcherSession(impersonate="chrome", stealthy_headers=True))

    async def parse(self, response: Response):
        for article in response.css('article', auto_save=True):
            title = (article.css('h2::text, h3::text').get('') or '').strip()
            if title and len(title) > 15:
                yield {
                    "title":   title,
                    "excerpt": (article.css('.excerpt::text, p::text').get('') or '').strip(),
                    "url":     article.css('a::attr(href)').get(''),
                    "source":  response.url,
                }

    async def on_scraped_item(self, item: dict):
        """Hook réel post-extraction (PAS process_item)."""
        async with httpx.AsyncClient() as c:
            await c.post(INGEST_ENDPOINT, json=item)
        return item

VeilleSpider(crawldir="./veille_crawl").start()   # pause/resume activé
```

---

## 4. Commande agent → Scrapling MCP

Workflow : `/scrape <url> <selector>` côté agent → serveur MCP Scrapling → données structurées.

Lancement du serveur :

```bash
scrapling mcp                                     # stdio (Claude Desktop / Cursor local)
scrapling mcp --http --host 0.0.0.0 --port 8000   # HTTP (serveur distant)
```

`mcp.json` :

```json
{
  "mcpServers": {
    "scrapling": {
      "command": "scrapling",
      "args": ["mcp"]
    }
  }
}
```

Instruction système (section outils de l'agent) :

```
Tu as accès au serveur MCP Scrapling. Pour extraire des données d'un site, appelle l'outil de
fetch approprié et retourne uniquement les données structurées, jamais le HTML brut.
```

---

## 5. Async batch haute volumétrie → PostgreSQL

```python
import asyncio, os
from scrapling.fetchers import AsyncFetcher
import asyncpg

async def batch_scrape_and_store(urls: list[str], selector: str):
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    async def scrape_one(url: str):
        try:
            page = await AsyncFetcher.get(url, block_ads=True)
            return [(url, el.css('::text').get(''), el.attrib.get("href", ""))
                    for el in page.css(selector, auto_save=True)]
        except Exception as e:
            print(f"[ERR] {url}: {e}")
            return []

    results = await asyncio.gather(*[scrape_one(u) for u in urls])
    flat = [row for batch in results for row in batch]

    try:
        await conn.executemany(
            "INSERT INTO scraped_data (url, text, href, scraped_at) "
            "VALUES ($1, $2, $3, NOW()) ON CONFLICT DO NOTHING",
            flat,
        )
    finally:
        await conn.close()
    print(f"Stored {len(flat)} rows")
```

---

## 6. Rotation de proxies (usage correct)

```python
import os
from scrapling.fetchers import StealthySession
from scrapling.engines.toolbelt.proxy_rotation import ProxyRotator

rotator = ProxyRotator(proxies=os.environ["PROXY_LIST"].split(","))

# proxy_rotator= sur la session — exclusif avec proxy=/proxies=
with StealthySession(proxy_rotator=rotator, headless=True) as session:
    page = session.fetch("https://target.com", block_ads=True)
```
