# Trading Desk-in-a-Box: Grok Bot managing a trading bot

Source: pasted article by @A_PirateI. It contains no performance numbers and the author says to measure your own. The Jev (TypeSafe) pricing ($0.042 per million input tokens, no metered output) and the Freqtrade MCP server's 17 tools are the article's claims, unchecked. This note is a summary, not financial advice.

## Core idea
Don't make the chat agent trade. Keep the existing trading bot (Freqtrade, Hummingbot, etc.) as the only thing that can place orders. Add a **decision layer** (Jev) between research and execution.

```text
Grok Bot does the research -> Jev makes the call -> trading bot executes -> human owns the dangerous edges
```

Jev (per TypeSafe): "unstructured state in, typed probabilistic decisions out." It picks from a fixed list and returns probabilities and confidence: choose, score, classify, route, verify. It doesn't write.

## The desk
| Seat | Role |
|---|---|
| SCOUT (Grok Bot) | News, X, funding, open interest on whitelisted pairs; morning brief |
| CHART (Grok Bot) | Structure and levels on the whitelist; trade plan (entry, stop, target) |
| Jev | Routes work, checks signals, reviews closed trades, guards risky actions |
| Freqtrade | Only seat that places orders; runs `dry_run` until it earns more |
| DESK MANAGER (Grok Bot) | Front door: coordinates, tracks status, writes morning and evening reports; never trades |

Start with the jobs you have; don't connect every tool on day one.

## DESK MANAGER charter (essentials)
Turn each request into one objective, find what's missing, assign each part to the owning seat, delegate rather than do, track done/evidence/blockers/left. Come back only when complete, approval needed, or blocked. Never pretend delegated work happened, never call a trade closed without fills, never touch the bot's settings.

## Desk Bridge plugin (read-only)
Build an Agent Plugin with secrets as env vars (`FREQTRADE_API_URL`, `FREQTRADE_USERNAME`, `FREQTRADE_PASSWORD`, `TYPESAFE_API_KEY`), never hardcoded or logged. Bot API listens on `127.0.0.1` only.

Read tools: `bot_status` (GET /status), `bot_profit` (GET /profit), `bot_balance` (GET /balance), `bot_whitelist` (GET /whitelist).

Jev tools, each a Choice question:
- `jev_route_task`: objective, completed_work, blockers, available_seats. Returns selected_seat, probabilities, confidence.
- `jev_check_signal`: pair, direction, timeframe, evidence, source_quality, known_conflicts. Choices: accept, verify_more, reject.
- `jev_review_trade`: original_plan, bot_fills, what_was_verified, known_gaps. Choices: closed_as_planned, verify_more, mismatch.
- `jev_guard_action`: proposed_action, target, money_at_risk, reversibility, existing_approval_policy. Choices: allow, confirm, human_review, deny.

No tool may place, change or cancel an order. Add a short skill saying when each tool is used. Test every tool with one safe example in `dry_run` before packaging.

Rationale: the existing open-source Freqtrade MCP server includes placing trades, start/stop and config reload, which is why the author wouldn't plug it in as is.

## Design rules
- **Jev chooses only from options that exist now.** Bot stopped means no `trading_bot` option; pair not on whitelist can't be routed; money-moving steps keep `human_review` on the menu.
- **Ask a routing question, not a trade question.** "Which seat owns the next step?", not "Should I go long?" The system owns the workflow; the model owns one fuzzy decision.
- **Signal gate.** The signal doesn't grade itself. CHART submits evidence; Jev returns accept, verify_more or reject; verify_more goes back to CHART or SCOUT with the gap named.
- **Close gate ("No fill, no done").** Before reporting a trade: restate plan, pull fills, put real numbers next to the plan, list what wasn't verified, run `jev_review_trade`. Typed output guarantees the format, not that the call is right: Jev is a gate, not an oracle.

## Autonomy levels
| Level | Scope | Rule |
|---|---|---|
| 0 Read | prices, balances, status, history | automatic |
| 1 Prepare | trade plan, levels, draft report | automatic |
| 2 Reversible | pause new entries, blacklist a pair | allowed and logged; `jev_guard_action` decides allow or confirm |
| 3 Trade | bot opens a position within its limits | human approval until proven in dry_run |
| 4 Money / irreversible | raise stake or leverage, change stop, new API key, transfer, withdraw | human, always |

Hard limits live in the bot's config, which no agent can edit, not in a prompt:
```json
{
  "dry_run": true,
  "max_open_trades": 3,
  "stake_amount": 50,
  "stoploss": -0.03,
  "force_entry_enable": false,
  "api_server": {
    "enabled": true,
    "listen_ip_address": "127.0.0.1",
    "listen_port": 8080,
    "username": "your-own",
    "password": "your-own"
  }
}
```
Exchange API key has withdrawals disabled, so Level 4 isn't reachable from the desk.

## Automate only after it works once
Run by hand, make reliable, save as a skill, then automate. Start with five routines:
1. **Morning brief** (SCOUT, daily): news, funding, OI on the whitelist; separate facts from noise; pass claims through the signal gate; return only what's worth attention.
2. **Setup triage** (CHART + Jev): strongest setup, plan, `jev_check_signal`, stop before any order.
3. **Position watch** (DESK MANAGER, every 30 min): read `bot_status`, compare to plans, speak only on change.
4. **Evening report** (DESK MANAGER, 21:00): signals found/checked/rejected, trades with fills, one thing that worked, one to change.
5. **Weekly review** (DESK MANAGER + Jev, Sundays): each Jev decision next to what the market did after; where the gate was too loose or strict.

## Packaging
```text
/trading-desk-in-a-box
  START-HERE.md  DESK-POLICY.md  RISK-LIMITS.md  APPROVALS.md
  /bots    desk-manager.md  scout.md  chart.md
  /jev     routing.md  signals.md  trade-review.md  guard.md
  /bridge  SETUP.md  freqtrade-config.example.json
```
Grok Bot shares Bots as templates, but a template is a blueprint: secrets, personal data and custom code don't travel, so a custom plugin like Desk Bridge needs its own SETUP.md.

## Test
Run the full desk in `dry_run` for 7 days on 3 pairs, every setup through the signal gate, every closed trade through the close gate, no live orders, daily report at 21:00. Measure: routing accuracy, signal escapes, false closes, human interruptions, recovery when the bot API or exchange is down. Each failure becomes a new test. Don't publish confidence numbers you didn't get from real runs.

## Related notes
- `grok-bot-one-person-company.md`, `grok-bot-opus-10-step-tutorial.md` (approval line and data line)
