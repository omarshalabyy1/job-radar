# Grok Bot and agent-team notes: index and cross-reference

Twelve condensed notes, written 2026-11-08 from pasted articles. The decision document built from them lives in the job-radar repo: `docs/superpowers/specs/2026-11-08-bot-team-design.md` (branch `docs/bot-team-design`, not committed).

## The notes

| Note | Angle | Trust |
|---|---|---|
| [grok-bot-masterclass.md](grok-bot-masterclass.md) | How the product is built; vs Hermes; setup | Best single source; cites docs I could not reach |
| [grok-bot-first-agent-team.md](grok-bot-first-agent-team.md) | Chief plus specialists, skills, routines, borders | Mostly restates official guidance |
| [grok-bot-72-hour-field-notes.md](grok-bot-72-hour-field-notes.md) | Rules, verification, routines, permissions from streams | Third-party repo and author's own MCP server: unreviewed |
| [grok-bot-reduce-token-bill.md](grok-bot-reduce-token-bill.md) | 12 settings to cut usage | Author's own usage claim; menu paths differ |
| [grok-bot-opus-10-step-tutorial.md](grok-bot-opus-10-step-tutorial.md) | Opus 5.5 as the brain; charter, data line, approval line | Model switch verified; "default for every bot" is staff-reported |
| [grok-bot-engineering-team-build.md](grok-bot-engineering-team-build.md) | Four coding bots, board on disk, file handoffs | Promotes InsForge; handoff template never shown |
| [grok-bot-for-engineering.md](grok-bot-for-engineering.md) | A team engineer's own fleet | Self-reported numbers |
| [grok-bot-eight-bot-team.md](grok-bot-eight-bot-team.md) | A solo founder's eight bots and prompts | Promotional; income claims unverified |
| [grok-bot-one-person-company.md](grok-bot-one-person-company.md) | Handbook plus six bots, 7-day setup | Prices and limits unchecked |
| [grok-bot-for-creators.md](grok-bot-for-creators.md) | Content workflow | Author-affiliated results |
| [grok-bot-trading-desk-in-a-box.md](grok-bot-trading-desk-in-a-box.md) | A decision layer between research and a trading bot | Jev pricing unchecked; not financial advice |
| [ai-agents-monetization-guide.md](ai-agents-monetization-guide.md) | General agent architecture and selling it (Spanish source) | Not Grok-specific; no figures |

Pasted twice, saved once: the Opus 10-step tutorial and the engineering-team build.

## Themes that recur, and where to read them

| Theme | Where |
|---|---|
| One bot, one job; add bots only when a role is stable | first-agent-team, eight-bot-team, one-person-company, 72-hour |
| Approval line by reversibility (draft freely, a human clicks anything external, financial or destructive) | opus-10-step, one-person-company, 72-hour, trading-desk |
| Shared computer means shared credentials; not a security boundary | masterclass, one-person-company, first-agent-team, eight-bot-team |
| Skill first, then routine; test before scheduling | masterclass, first-agent-team, 72-hour, opus-10-step |
| Verification is the job (machine-readable proof, "no fill, no done") | 72-hour, engineering-team-build, trading-desk, for-engineering |
| State in files, messages carry paths | engineering-team-build, first-agent-team, masterclass |
| Cost control: fewer, rarer runs; one chat per task | reduce-token-bill, 72-hour, for-engineering (P0 warning) |
| Daily or weekly self-review by an ops bot | for-engineering, first-agent-team, one-person-company, 72-hour |
| Data line: what the bot must not read | opus-10-step, one-person-company |

## Conflicts and open questions across the notes

- **Auto-merge:** for-engineering lets high-confidence, low-risk PRs merge; the other notes say never merge. Pick per repo.
- **Settings path:** reduce-token-bill says Settings, Bot, Custom Rules; masterclass and 72-hour say Settings, General, Agent or Auto-review. Check the app.
- **Cursor deal date:** articles say August; the web check found an announcement on 2026-06-16.
- **Bot and routine caps (50, 50), Auto review:** no official source found.
- **Cost figures:** the articles' per-ticket and per-job numbers are unverified.

## Verification status

See the "What was verified" table in the job-radar spec. Short version: product existence, launch date, the Musk post and the Cursor deal are confirmed by secondary sources; limits, pricing, data flow to the model provider, and every productivity or income number are not.
