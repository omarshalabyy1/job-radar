# Scraping skills

Claude Code skills a session opened in this repo can use when a site needs more than the daily
collect does (the `/job-radar` skill sends Claude here when you call it). Start with
`scraping-orchestrator`, which picks the right one for each step.

| Skill | From | License |
|---|---|---|
| scraping-orchestrator | written for job-radar's owner | - |
| web-scraping | [yfe404/web-scraper](https://github.com/yfe404/web-scraper) | MIT |
| scrapling | [Thanane15M/scrapling-skill](https://github.com/Thanane15M/scrapling-skill) | MIT |
| scrapling-fetcher | [Cedriccmh/claude-code-skill-scrapling](https://github.com/Cedriccmh/claude-code-skill-scrapling) | MIT |
| crawl4ai | [brettdavies/crawl4ai-skill](https://github.com/brettdavies/crawl4ai-skill) | MIT or Apache-2.0 |
| playwright-skill | lackeyjb's playwright-skill | MIT (its package.json); run `npm install` in it before first use |

The orchestrator also names a `scraping` skill; it carries no license, so it is not copied here.
In crawl4ai's SDK reference, an example API key is replaced with a placeholder.
