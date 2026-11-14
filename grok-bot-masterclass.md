# Grok Bot masterclass: how it works, vs Hermes Agent, and setup

Source: pasted article by @_avichawla. It cites the official docs (docs.x.ai/grok-bot/get-started) but I haven't checked them. The machine specs (Debian, ~8 vCPU, ~16 GB RAM, ~100 GB disk, no GPU) come from one user's report, not SpaceXAI. Competitor behavior (Devin, Manus, ChatGPT Work) is the author's description.

## What a Bot is
A saved role plus a memory plus a thread. It is not a separate machine, container or model instance. Defined by a short name, one primary job, and a description of how it should work. It keeps stable preferences, important facts and summaries, not a replay of every message.

Office analogy: the cloud computer is the office, each Bot a coworker with a desk, with one filing cabinet and one set of keys. Shared keys make handoffs free and make the whole roster share consequences.

## How it's built
- **One persistent cloud computer per user account** (browser, filesystem, terminal), shared by all Bots, running in SpaceXAI's cloud. Closing the laptop doesn't stop a turn or routine. Each Bot has its own screen; one Bot runs one computer-use task at a time.
- **Durability contract:** shared workspace `/workspace`, files, browser state and supported sign-ins survive normal updates and recovery. Temp dirs, manually installed packages and uncommitted app state carry no guarantee.
- **Sharing:** cookies and signed-in sessions, files, and command-line credentials are visible to every Bot. Don't place a credential or file there that another Bot shouldn't use.
- **Action layer:** connectors (shown as Plugins, account-wide) first; browser as fallback for everything else, which inherits site fragility (layout change, consent prompt, session timeout).
- **Auth and takeover:** for passwords, passkeys, 2FA, CAPTCHAs, payment or identity checks, the Bot hands you the computer: open Agent Computer, take control, do only the blocked step, return control, tell it to continue. Supported connections use a secure secret request (masked, not in the transcript, not shown to the model). Never paste a password or one-time code into chat.
- **Memory:** per Bot; internals unpublished. Duplicating a Bot copies profile, settings, enabled skills, routines and avatar, not conversation history or learned memory.

## Creating a Bot
Cmd+N or "+", Create new agent, then Bot actions, Edit Profile (name, title, description, avatar). Description holds rules that should always hold ("never send external messages without approval"); the thread holds task-specific instructions. Specific jobs beat general ones ("Talent Scout", "Expense Manager" vs "General Helper"). Limit: 50 Bots and group chats combined; start small.

## Skills
Reusable instructions (steps, decision rules, expected output, safety boundaries), shared across Bots, though a Bot may still need the connector or login. Reference with `/`; mention Bots, groups, routines and connectors with `@`. A good skill covers: when to use it, inputs and access, sequence, how to validate, what to return, what needs approval.

**Teach a task:** in a one-to-one chat's computer view, describe the result, do the workflow once, stop recording. Records visible interaction up to 10 minutes, no microphone. Re-test after any site, connector or source change.

## Routines
Assign a workflow to one Bot and say when to run: schedule (name time zone), or event trigger via Cursor account integrations (e.g. Slack message, GitHub notification), which are separate from the Slack and GitHub plugins and may need their own connection flow. A good request names schedule and time zone, input source, expected result, approval boundary, and what to do when a source is missing. Press Test first; a test performs real work. Limits: 50 routines per Bot, app keeps the 20 most recent run records per routine. Deleting a routine is permanent.

## Multiple Bots
Groups hold 2-6 Bots; use for visible handoffs; name who owns the next step; `@` a Bot when one owns the request, everyone-mention only for genuine group updates. Bots can also message each other asynchronously outside groups. Handoff messages in groups are text-only, so send images directly. One Bot per stage; too many parallel handoffs cause duplicate work and noise (shows up past four or five Bots). A direct message from you outranks background work and can redirect the current turn.

## Auto review
Model-based review of tool calls and computer actions before they run: Require Approval rules always stop matching actions; Always Allow rules proceed only if the review finds no other reason to stop; Require Approval wins on conflict. It complements least privilege, not replaces it. Personal rules are stored on the current desktop and synced to its computer, so a second installation needs its own check.

## Local machine
A Bot runs local commands only when enabled and you approve. Setting: Settings, General, Agent, Execution on Local Computer. The article's advice: set to never allowed unless a Bot has a specific reason to touch local files. Doesn't restrict the cloud computer.

## Grok Bot vs Hermes Agent
| | Grok Bot | Hermes Agent |
|---|---|---|
| Hosting | Managed; machine provisioned, not visible | Open source, self-hosted (Mac Mini, VPS) |
| Memory/skills | Not exposed as files; behavior-described | Files you can read and edit (SOUL.md, MEMORY.md, USER.md with character limits, SQLite sessions, Markdown skills with frontmatter) |
| Maintenance | None for you | You run it; Curator prunes unused agent-written skills, GEPA evolves skills offline from traces |
| Multi-agent | First-class: group chats, async handoffs, ownership passing, shared browser session | You assemble it |
| Isolation | Shared by design | Real isolation |

Trade: inspectable files and isolation (Hermes) versus not running the machine (Grok Bot). Other hosted tools (Devin: snapshot per session; Manus: sleeping sandbox; ChatGPT Work: browser cookies) persist one thing for one agent, usually per session or project.

## macOS setup
1. Open the Grok Bot access page, download for your Mac, open the disk image, drag to Applications, launch (confirm Open if asked).
2. Get started, authenticate in the browser (SSO orgs use their usual flow), return to the app.
3. Onboarding explains Bots, the shared computer, routines, and asks which tools you use (shapes suggestions; connects nothing). The cloud computer provisions in the background.
4. Meet a future teammate: pick a suggestion or Create your own (short name, one job, description).
5. Strong first request has five parts: outcome, sources, constraints (avoid or ask about), deliverable, where it stops for you.
6. First test with no login: attach a document and ask for a structured summary with citations (~5 minutes; confirms the machine is healthy).
7. For auth, use takeover once; for supported services, install a connector in Settings, Plugins instead.
8. When a result is repeatable, save it as a named skill (sources, output format, approval rules); only then create a routine.

## Takeaways
- State moved out of the context window into a machine that stays running: free handoffs and shared credentials are the same fact.
- Path the docs prescribe: one narrow Bot, one real task, correct until reviewable, then skill, then routine, with consequential actions behind approval. A routine inherits every unstated assumption of the task it came from.

## Related notes
`grok-bot-one-person-company.md`, `grok-bot-first-agent-team.md`, `grok-bot-engineering-team-build.md`, `grok-bot-72-hour-field-notes.md`
