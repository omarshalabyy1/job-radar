---
name: scrapling
description: >
  Expert Scrapling — framework Python de web scraping adaptatif gérant tout depuis une requête
  simple jusqu'à un crawl full-scale avec pause/resume. Utiliser pour : choisir le bon fetcher ou
  session (Fetcher / StealthyFetcher / DynamicFetcher + variantes Session / Async), activer le
  self-healing (adaptive=True / auto_save=True), bypass Cloudflare Turnstile, rotation de proxies
  (ProxyRotator), spiders multi-sessions, streaming d'items, export JSON/JSONL, intégrer le serveur
  MCP natif avec Claude/Cursor, utiliser la CLI (extract / shell / mcp), impersonation TLS
  (chrome/firefox), HTTP/3, migrer depuis BeautifulSoup/Scrapy, déployer via Docker. Déclencher
  aussi pour : "scraper auto-réparant", "sélecteur cassé après redesign", "bypass anti-bot Python",
  "session scraping persistante", "crawl pause resume Python", "scrapling install", "scrapling mcp",
  "scrapling spider", "scrapling session", "impersonate chrome scraping", "ProxyRotator".
---

# Scrapling — Framework Python de Scraping Adaptatif

**Cible : Scrapling v0.4.x (vérifié sur 0.4.9) · Python 3.10+ · Licence BSD-3-Clause (auteur : Karim Shoair / D4Vinci)**
Docs : https://scrapling.readthedocs.io · Repo : https://github.com/D4Vinci/Scrapling

> **Épingler la version** : `pip install "scrapling==0.4.9"`. La série 0.4 a introduit des changements d'API majeurs (spiders async, ProxyRotator). Un skill non versionné se périme silencieusement.

---

## Quand utiliser ce skill — et quand NE PAS

Utiliser pour : extraction HTML/DOM, contournement d'anti-bot, crawl multi-pages, self-healing de sélecteurs, migration depuis BS4/Scrapy, intégration MCP.

Ne **pas** utiliser pour : APIs JSON publiques (un simple `httpx`/`requests` suffit), automatisation d'actions authentifiées complexes (Playwright brut), ou tout scraping qui violerait les CGU / le `robots.txt` / le RGPD. Scrapling est fourni pour usage éducatif et recherche : respecter robots.txt, les CGU et ne pas collecter de données personnelles sans base légale.

---

## Installation

```bash
pip install "scrapling[fetchers]"   # recommandé : parser + fetchers
scrapling install                   # OBLIGATOIRE avant tout fetcher navigateur (Chromium + deps)
                                     # après un upgrade : scrapling install --force (rafraîchit les fingerprints)

pip install scrapling               # parser seul, sans réseau
pip install "scrapling[all]"        # fetchers + MCP + shell
pip install "scrapling[ai]"         # serveur MCP uniquement
pip install "scrapling[shell]"      # CLI shell + commande extract

docker pull pyd4vinci/scrapling     # ou : ghcr.io/d4vinci/scrapling:latest
```

> **Piège n°1** : oublier `scrapling install`. Sans lui, `StealthyFetcher`/`DynamicFetcher` échouent (pas de navigateur). `Fetcher` (HTTP pur) fonctionne sans.

---

## Choisir le bon outil — arbre de décision

```
Le HTML voulu arrive-t-il via une simple requête HTTP ?
├─ OUI ─ besoin d'un fingerprint navigateur (TLS + headers) ?
│        ├─ NON  → Fetcher / AsyncFetcher
│        └─ OUI  → Fetcher(impersonate='chrome')      # curl_cffi, AUCUN navigateur lancé
└─ NON (JS requis OU anti-bot)
         ├─ anti-bot (Cloudflare/Turnstile, DataDome…) → StealthyFetcher(solve_cloudflare=True)
         └─ SPA / rendu JS, sans anti-bot               → DynamicFetcher
```

Puis : **multi-pages ou état partagé ?** → passer en classe **Session** (cookies + navigateur conservés entre appels). **Crawl full-scale ?** → **Spider**.

| Besoin | One-shot | Session persistante | Async |
|--------|----------|---------------------|-------|
| HTTP rapide | `Fetcher` | `FetcherSession` | `AsyncFetcher` (one-shot async) |
| Bypass anti-bot | `StealthyFetcher` | `StealthySession` | `AsyncStealthySession` |
| JS lourd / SPA | `DynamicFetcher` | `DynamicSession` | `AsyncDynamicSession` |

> Note : il n'existe pas d'`AsyncFetcherSession`. Pour de l'HTTP async, utiliser `AsyncFetcher` dans un `asyncio.gather()`.

---

## Fetchers — exemples vérifiés

### HTTP rapide + impersonation TLS

```python
from scrapling.fetchers import Fetcher, FetcherSession

page = Fetcher.get('https://quotes.toscrape.com/')              # one-shot

with FetcherSession(impersonate='chrome') as session:          # session persistante
    p1 = session.get('https://site.com/page1', stealthy_headers=True)
    p2 = session.get('https://site.com/page2', impersonate='firefox135')

with FetcherSession(http3=True) as session:                    # HTTP/3
    page = session.get('https://site.com/')
```

### Bypass Cloudflare Turnstile

```python
from scrapling.fetchers import StealthyFetcher, StealthySession

page = StealthyFetcher.fetch('https://nopecha.com/demo/cloudflare')  # one-shot

with StealthySession(headless=True, solve_cloudflare=True) as session:
    page = session.fetch('https://protected-site.com', google_search=False)
    data = page.css('#content a').getall()
```

> `solve_cloudflare=True` lance un vrai navigateur (coût temps/CPU). Ne l'activer que si la cible est réellement protégée.

### SPA / JavaScript lourd

```python
from scrapling.fetchers import DynamicSession

with DynamicSession(headless=True, network_idle=True) as session:
    page = session.fetch('https://react-app.com', load_dom=False)
    data = page.xpath('//span[@class="text"]/text()').getall()
```

---

## Self-Healing Selectors (adaptive) — workflow en 2 temps

`adaptive=True` ne sait relocaliser un élément **que s'il a été enregistré** lors d'un run précédent. C'est un workflow en deux phases, pas un drapeau magique.

```python
# Phase 1 — premier run : on enregistre la signature de l'élément
products = page.css('.product-card', auto_save=True)

# Phase 2 — après redesign du site : on retrouve l'élément par similarité
products = page.css('.product-card', adaptive=True)

# Au niveau du fetcher entier
StealthyFetcher.adaptive = True
page = StealthyFetcher.fetch('https://example.com', headless=True)
```

> `percentage` (défaut 40) règle le seuil minimal de similarité accepté en mode adaptive. Ne pas y toucher sans raison : le calcul dépend de la structure de la page.

---

## Extraction & navigation

```python
# CSS (pseudo-éléments Parsel/Scrapy compatibles)
titles = page.css('h2.title::text').getall()
href   = page.css('a::attr(href)').get()

# XPath
price = page.xpath('//span[@class="price"]/text()').get()

# Style BeautifulSoup
items = page.find_all('div', class_='product')
items = page.find_all(['div', 'article'], class_='card')

# Recherche par texte
btn = page.find_by_text('add to cart', tag='button')

# Navigation DOM
parent   = element.parent
siblings = element.siblings
children = element.children
below    = element.below_elements()
similar  = element.find_similar()

# Chaînage
text = page.css('.quote')[0].css('.text::text').get()

# Génération auto de sélecteurs (utile en shell pour figer un sélecteur robuste)
css_sel   = element.generate_css_selector
xpath_sel = element.generate_xpath_selector
```

---

## Spiders — crawl full-scale

### Spider basique + export

```python
from scrapling.spiders import Spider, Response

class QuotesSpider(Spider):
    name = "quotes"
    start_urls = ["https://quotes.toscrape.com/"]
    concurrent_requests = 10

    async def parse(self, response: Response):
        for quote in response.css('.quote'):
            yield {
                "text":   quote.css('.text::text').get(),
                "author": quote.css('.author::text').get(),
            }
        next_page = response.css('.next a')
        if next_page:
            yield response.follow(next_page[0].attrib['href'])

result = QuotesSpider().start()            # -> CrawlResult
print(f"Scraped {len(result.items)} items")
result.items.to_json("output.json")
result.items.to_jsonl("output.jsonl")
```

### Hooks de cycle de vie (le vrai pipeline)

Scrapling **n'a pas** de hook `process_item`. Les hooks réels, à surcharger sur la classe `Spider` :

```python
class MySpider(Spider):
    async def on_scraped_item(self, item: dict) -> dict | None:
        """Pipeline post-extraction : nettoyage, validation, push DB/API.
        Retourner None pour drop l'item."""
        await save_to_db(item)
        return item

    async def is_blocked(self, response: Response) -> bool:
        return response.status in (403, 429) or "captcha" in response.body.lower()

    async def retry_blocked_request(self, request, response):
        return request                       # ré-essai (ex. via une autre session)

    async def on_error(self, request, error: Exception): ...
    async def on_start(self, resuming: bool = False): ...
    async def on_close(self): ...
```

### Multi-session spider

```python
from scrapling.spiders import Spider, Request, Response
from scrapling.fetchers import FetcherSession, AsyncStealthySession

class MultiSessionSpider(Spider):
    name = "multi"
    start_urls = ["https://example.com/"]

    def configure_sessions(self, manager):
        manager.add("fast",    FetcherSession(impersonate="chrome"))
        manager.add("stealth", AsyncStealthySession(headless=True), lazy=True)

    async def parse(self, response: Response):
        for link in response.css('a::attr(href)').getall():
            sid = "stealth" if "protected" in link else "fast"
            yield Request(link, sid=sid, callback=self.parse)
```

> Le moteur de crawl est **async**. Pour les cibles lourdes, privilégier des sessions async (`AsyncStealthySession`, `AsyncDynamicSession`) ; `lazy=True` diffère l'ouverture du navigateur au premier usage.

### Pause & resume

```python
QuotesSpider(crawldir="./crawl_data").start()
# Ctrl+C -> checkpoint sauvegardé · relancer la même commande -> reprise auto
```

### Streaming temps réel

```python
spider = QuotesSpider()
async for item in spider.stream():          # items au fil de l'eau + stats live
    await push_to_pipeline(item)
```

---

## ProxyRotator — usage correct

L'import top-level `from scrapling import ProxyRotator` **n'existe pas**. La rotation se branche via le paramètre `proxy_rotator=` du fetcher/session, et est **exclusive** avec `proxy=` / `proxies=` (sinon erreur).

```python
from scrapling.fetchers import StealthyFetcher, StealthySession
from scrapling.engines.toolbelt.proxy_rotation import ProxyRotator

rotator = ProxyRotator(proxies=[
    'http://user:pass@proxy1:8080',
    'http://user:pass@proxy2:8080',
])

# Brancher le rotator sur la session (PAS proxy=rotator.next())
with StealthySession(proxy_rotator=rotator, headless=True) as session:
    page = session.fetch('https://target.com', block_ads=True)   # block_ads ~3500 domaines

# Récupérer un proxy manuellement si besoin : rotator.get_proxy()  (il n'y a pas de .next())
```

> Cloudflare entreprise → proxies résidentiels (NodeMaven, BirdProxies, Evomi, DataImpulse).
> Akamai/DataDome/Kasada/Incapsula → service dédié type Hyper Solutions (hypersolutions.co).
> **v0.4.9** corrige un bug où le `proxy` de session HTTP était ignoré silencieusement (fuite d'IP réelle). Mettre à jour si vous étiez < 0.4.9.

---

## Serveur MCP — Claude / Cursor

```bash
pip install "scrapling[ai]"

scrapling mcp                                  # transport stdio (défaut)
scrapling mcp --http --host 0.0.0.0 --port 8000   # transport streamable-http
```

**Avantage tokens** : l'agent reçoit les données extraites, jamais le HTML brut.

`mcp.json` (config Claude/Cursor, transport stdio) :

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

> Le module `scrapling.mcp` et les variables d'env `SCRAPLING_*` n'existent pas : c'est la commande CLI `scrapling mcp` (classe `scrapling.core.ai.ScraplingMCPServer`).
> Docs MCP : https://scrapling.readthedocs.io/en/latest/ai/mcp-server/

---

## CLI

```bash
scrapling shell                       # IPython interactif : debug sélecteurs, preview navigateur
scrapling shell https://example.com

scrapling extract get 'https://example.com' out.md
scrapling extract get 'https://example.com' out.txt --css-selector '#content' --impersonate 'chrome'
scrapling extract fetch 'https://example.com' out.md --no-headless
scrapling extract stealthy-fetch 'https://cf-site.com' out.html --solve-cloudflare

scrapling --version
```

---

## Migration BeautifulSoup → Scrapling

| BeautifulSoup4 | Scrapling |
|----------------|-----------|
| `requests.get(url)` + BS4 | `Fetcher.get(url)` (tout-en-un) |
| `soup.find_all('div', class_='x')` | `page.find_all('div', class_='x')` |
| `soup.find('h2').text` | `page.css('h2::text').get()` |
| `el.get('href')` | `el.attrib['href']` ou `page.css('a::attr(href)').get()` |
| `el.find_next_sibling()` | `el.next_sibling` |
| (aucun anti-bot) | `StealthyFetcher.fetch(url, solve_cloudflare=True)` |
| (aucun self-healing) | `page.css('.x', adaptive=True)` |

---

## Pièges fréquents (checklist)

- [ ] `scrapling install` exécuté avant tout fetcher navigateur ?
- [ ] Version épinglée (`==0.4.9`) — l'API 0.4 a changé vs 0.3 ?
- [ ] ProxyRotator branché via `proxy_rotator=`, jamais avec `proxy=` ?
- [ ] `adaptive=True` précédé d'un run `auto_save=True` ?
- [ ] Spider : pipeline dans `on_scraped_item`, pas `process_item` ?
- [ ] `solve_cloudflare=True` réservé aux cibles réellement protégées (coûteux) ?
- [ ] robots.txt / CGU / RGPD respectés ?

---

## Benchmarks (ordre de grandeur, parsing de 5000 éléments)

| Librairie | Temps | vs Scrapling |
|-----------|-------|--------------|
| **Scrapling / Parsel** | ~2 ms | 1x |
| Raw lxml | ~2.5 ms | ~1.3x |
| PyQuery | ~24 ms | ~12x |
| Selectolax | ~83 ms | ~41x |
| BS4 + lxml | ~1.6 s | ~780x |

Chiffres officiels du repo (susceptibles d'évoluer entre versions). Le gain réel dépend de votre charge ; benchmarkez votre cas.

---

## Parser standalone (sans réseau)

```python
from scrapling.parser import Selector

page = Selector("<html>...</html>")
titles = page.css('h2::text').getall()
```

---

## Références

→ `references/patterns.md` — patterns d'intégration réutilisables (monitoring prix, crawl → PostgreSQL, MCP → agent, async batch).
