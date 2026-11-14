# Grok Bot for Engineering (SpaceXAI engineer's write-up)

Source: pasted first-person article by a SpaceXAI engineer on the Grok Bot team (the original behind the "Jenny" and "200+ agents" anecdotes in `grok-bot-first-agent-team.md`). Productivity claims (2,000+ PRs in a month, iOS v0 in three weeks, fleet of 200+ agents versus 15 by hand) are the author's own and unverified; the author is also promoting the product.

## Core idea
Treat Grok Bot as a capable engineering intern with its own computers that manages coding agents for you and learns how you work. Each engineer bot manages Cursor cloud agents the way you would, so you stop context-switching.

## The bots
Five engineer bots, one domain each (mobile shared layer / iOS, desktop client and CI/CD, infrastructure and unowned user issues, Android, the harness), plus **Jenny**, head of operations, the only bot that writes no code. Bots can cross areas but each has its own memory and limited context, so they perform best inside one domain.

## How a task flows
1. Task arrives from you or Slack.
2. Bot starts a Cursor cloud agent with your skills invoked, a thorough prompt, and the proof expected. Bots can also call extra skills on your guidance (e.g. design, React Native best practices, review, product).
3. Bot monitors the agent's transcript and artifacts (screenshots), queues messages or interrupts, and keeps going until the goal is met (e.g. "verify the screenshot shows the change, with before vs. after proof").
4. Bot unblocks flaky or stalled runs itself; it escalates only when it lacks permissions.

Cloud agents can also run on your own worker machines (a spare Mac mini, or a private worker with VPN or special config, enabling iOS Simulator and screenshots).

**Complete feedback loop** is the key: agents should be able to launch a dev instance and drive the stack end to end (Chrome DevTools, CLI, Apple Accessibility). Example: voice API hooked into the agent's system audio I/O to test speech-to-speech features.

## Beyond the context limit: shared Notion board
Each engineer bot manages a shared Notion database. Every 30 minutes it reviews each PR for:
- Bugbot comments or security findings (verify each is legitimate)
- failing CI
- merge conflicts

Problems go back to the cloud agent and the row returns to "Working". Clean PRs are marked "Ready for Review" and trigger a code-review run. High confidence plus low blast radius merges automatically; otherwise the human reviews code and proof later.

Note: this auto-merge rule is the author's choice for their own repos. It differs from the "never merge" approval lines in the other notes; decide your own limit.

## Jenny, the ops bot
- 5 a.m. daily 1:1 with every bot: review playbook, surface blockers, reinforce expectations.
- On a mistake: root-cause analysis and postmortem, update the playbook, announce changes to the other bots.
- Onboards new bots: creates the bot, shares team rules, asks teammates to help.

## Bonus routines
**Nightly audits (3 a.m.):** code cleanup, dead logic, load time, bundle size, producing PRs for the morning. Other ideas: security, CI/CD build time, internationalization, parity across clients (iOS vs desktop), catch-up audits (summary and curated PR list for the last 24 hours). Favorite open prompt: "You have six hours tonight. Build whatever you want. Have fun!"

**P0 process:** saying "this is P0" starts a temporary routine that checks the agent's transcript every five minutes and steers it when it wastes time. It can burn tokens fast; use only for true urgency.

## Tips
1. Give cloud agents a complete feedback loop; if they can't, have them run the flow themselves and save what they learn as a repo skill.
2. Treat it like a talented intern: ask it to do its homework and study unfamiliar areas; no long prompts needed.
3. If you do something more than once a day in a clear pattern, discuss delegating it.
4. Daily meetings help bots retain complex workflows past the context limit.
5. Be more hands-off, like building trust in self-driving: freedom where safe, caution where risky, and don't stop them from trying after a failure.
6. Build a bot-mistake review pipeline (an ops bot analyzing thinking traces) so mistakes don't repeat.

## Related notes
- `grok-bot-first-agent-team.md`, `grok-bot-opus-10-step-tutorial.md`, `grok-bot-one-person-company.md`
