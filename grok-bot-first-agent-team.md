# Grok Bot engineering: build your first agent team from scratch

Source: pasted article (quotes the official launch post and several X posts). Product details, usage behavior and team anecdotes (an engineer going from ~15 to 200+ agents, the "Jenny" ops bot) are the article's, not checked. Overlaps with `grok-bot-one-person-company.md` and `grok-bot-opus-10-step-tutorial.md`.

## What a bot is
Named teammate with a role, on a persistent cloud computer (browser, filesystem, terminal) that signs into apps and finishes work in the real tool. Keeps working with your laptop closed. Memory, files, sessions and preferences carry over. No workflow builder: you set it up by messaging it (10-15 minutes).

Three ways to put a bot to work: message it, give it a routine (schedule or event trigger), or have another bot message it. The goal is to move from the first to the third.

All bots share one computer: same files, sessions and logins, separate screen per bot. Handoffs work easily, which is also why you need rules.

## Why not one super-agent
It drowns in context and can't be judged. Each bot needs a target, a safety limit, a quality bar, and a check for whether its work helped. Measure outcomes, not activity. Too many bots (14 chats to babysit) fails too: the answer is structure.

## The team (start with four)
| Bot | Job |
|---|---|
| Chief of Staff | Only bot you talk to. Splits goals into tasks, hands them off, sends one end-of-day update. |
| Researcher | Morning scan of your sources; findings with a link for every claim. |
| Writer | Drafts in your voice, drafts only. |
| Reviewer | Checks drafts against sources; lists only blocking issues. |
| Ops (later) | Daily check-in, catches mistakes, updates the rules. |

The bot that writes never checks. Every stage has one owner. Add a shared Notion table board (task, owner, status) so you scan one page, not five chats.

## Step 1: Chief of Staff
Create with the plus button or Cmd/Ctrl+N, or promote an existing bot. First message needs: the job, the sources, the approval limits, and "remember all of this".
```text
You're my Chief of Staff.
Your job: take my goals, split them into tasks, hand each one to the right bot,
and send me one update at the end of the day.
Your sources: my Gmail, Notion and Slack.
Your limits: never send, publish, buy or delete anything without my OK.
Remember all of this.
First task: [a real task]. Done when: [the finish line].
```
Each task is a small contract: objective, scope, what not to touch, expected output, done condition. Memory isn't a safety net; back limits with approvals and **Auto-review** (a separate reviewer checking risky actions against plain-language rules; allow, block, or bring to you).

## Step 2: connect tools once
Connect apps (Gmail, Slack, Notion, Linear, etc.), same MCP servers, plugins and skills as Cursor. Apps are account-wide, so connect only what the team needs. Tokens stay on the connector side. For sites with no app: logins via Vault, a connected password manager, or a Secure Form (values filled without the bot seeing them). When a site needs a human (passkey, CAPTCHA, bank check), the bot hands you the computer. Never paste a password into chat. Bots can have their own email address; anything a routine sends from it waits as a draft until you press Send.

## Step 3: specialists, one job each
New bot only when the work has its own goal, tools, working style, approval boundary or schedule; otherwise it's a task. Write a job description with a strong "never" list that names the other bots.
```text
You're the Researcher.
You own: the morning scan of [sources].
You deliver: 5 findings by 9:00, each with a link to the source.
You never: write drafts, contact anyone or post anything.
If a source is down, say so instead of guessing.
Remember all of this.
```
Add bots only when a role is stable or a bottleneck; ask the Chief before it builds several. Share a working bot as a template.

## Step 4: handoffs
Bots message each other directly, or use a group chat of 2-6 bots and mention one to give it the task. Give each stage one owner in a single kickoff; two bots on the same stage just duplicates work. Handoffs carry proof (sources, screenshots, test results, action log): "done" is a claim. A retry must change something. Approval requests, login requests and email/Slack drafts don't appear in the group chat; check each bot's own chat. "stop now" ends work but doesn't undo it.

## Step 5: skills
A skill is reusable instructions (steps, decision rules, expected output, approvals), shared across all bots. Do the task until good, then ask the bot to save it with a name. **Teach a task** (screen recording up to 10 minutes) makes a draft skill from one example: add decision rules, failure handling and approval points yourself and test on something safe. Rule of thumb: same pattern more than once a day means skill.

## Step 6: routines
A routine runs one bot's workflow on a schedule or on an event (Slack message, GitHub/Linear event, email, webhook). Order: one-time task, make reliable, save as skill, then automate. A good routine prompt names the skill, time and time zone, input, output, approval line, and what to do if a source is missing.
```text
Every weekday at 8:00 AM, run the "morning scan" skill on [sources].
Post 5 findings with links in this chat. Don't contact anyone.
If a source is down, report it instead of using old data.
```
Press Test before trusting it (a test does real work). Every run spends usage; hourly schedules or busy-channel triggers can burn a week of usage in a day. Automate preparation first, keep execution behind approval. If a run seems skipped, check run history before rewriting.

## Step 7: memory and daily check-in
Each bot keeps its own memory and a dated work log. Tell it what to remember; ask what it remembers; tell it to forget wrong things. Memory is not a source of truth: reopen real data for big decisions. The article's ops-bot pattern: one bot that writes no code meets each bot daily, reviews the playbook, finds root causes of mistakes, updates the playbook and tells the others.

## Borders on the shared computer
1. List every signed-in account; assume any bot can reach any session.
2. Keep secrets and customer data off unless every bot with access may see them.
3. One folder per role plus one shared folder (organization, not a security boundary).
4. Pass context on purpose (DM, group chat, named handoff file), not via leftover tabs.
5. Before deleting a bot: pause routines, revoke access, remove its files, check open sessions. Deleting erases memory and routines but not files it left behind.

## Common mistakes
One bot owning everything; twenty bots on day one; talking to every bot yourself; automating before it works once; no delivery step (say what is delivered, where, and that failures get reported); full freedom on day one; large group chats and broad triggers (every bot message, including bot-to-bot, counts toward usage).

## First week
1. Create the Chief of Staff, one real task.
2. Connect tools, correct what it got wrong.
3. Add the Researcher (one source list, one deliverable).
4. Add Writer and Reviewer, group chat, run the kickoff once by hand.
5. Save the result as a skill.
6. Turn the morning scan into a routine, press Test.
7. Ask every bot what it remembers about you; fix errors.

End state: you talk to one bot; routines handle repeats; nothing irreversible happens without your yes.
