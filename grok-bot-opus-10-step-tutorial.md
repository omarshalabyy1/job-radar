# Grok Bot on Claude Opus 5.5: the 10-step tutorial

Source: pasted article. Every factual claim below (the Oct 7 model switch, the SpaceX-Cursor deal, benchmark gaps, pricing, plan bundling) is the article's, not checked. The article itself says SpaceXAI has not published data-sharing terms, usage-limit impact, or dates for Midjourney and Suno.

## The claim
On Oct 7, Grok Bot reportedly split harness from brain: SpaceX keeps the harness (cloud computer, logins, memory, routines) and routes tasks to the best back-end model, with Claude Opus 5.5 as the default for every bot. Bots can also spawn cloud agents running Cursor's models. Midjourney and Suno were named but have no date; build only on Opus 5.5, which is described as confirmed.

Benchmark point: the article says Terminal-Bench shows almost a 3x gap versus Grok 4.6 (measured by Artificial Analysis, a month apart from Grok 4.7, so not like for like).

## Steps
1. **Understand the change.** Harness is the product; the brain is swappable.
2. **Install, meet the Chief.** Mac, iPhone, iPad app. Sign in with Grok or Cursor account, bundled with most SuperGrok and paid Cursor plans. No separate Claude subscription. Start with one general bot ("Chief") and a task you can verify in 30 seconds.
3. **Write a charter.** A bot is a role, not a prompt. Name it for a job; say what it owns, what good looks like, where it stops and asks. Stronger brain means hand over the whole job, but "where you stop" stays just as strict.
   ```
   You are my Research Lead.
   // owns: run the job end to end, primary sources, open every cited page,
   //   cross-check numbers across 2+ sources, draft a 1,500-word brief
   // good: every number has a link, conflicts flagged not averaged, draft in Drive
   // stops: never publish, never email; park unresolved conflicts and ask
   ```
4. **Connect tools once, draw the data line.** One-click plugins (Notion, Slack, Google Drive, AWS Agents, AWS SageMaker, Browserbase, Composio, Context7, custom). Connections are account-level, so every bot gets them. What a bot reads now goes to Anthropic for that step. Add to every charter:
   ```
   // the data line
   Do not open, read, or summarize:
     - anything in my /Legal or /Finance Drive folders
     - emails from my lawyer, accountant, or bank
     - any message with passwords, seed phrases, or 2FA codes
   If a task needs one of these, stop and ask me first.
   Use only what the task needs from a document.
   ```
5. **Hand off the login, never the password.** Bot works in its cloud browser until a login wall, hands you the screen, you sign in, it continues in the same session. Never paste a password into a message.
6. **Show once, then make a routine.** Record a recurring, multi-tool, stable workflow once; bot saves the steps. Fire it by schedule ("every weekday at 7am, brief from calendar, inbox, #launches") or trigger ("email from unknown domain mentioning pricing: draft reply from template and park it"). Shortcut: say "Run this every week" right after a task you liked.
7. **Specialists that spawn Cursor agents.** Split bots by domain, not task size (separate memory and context). Example Repo Maintainer: on a "bug" issue, reproduce with a failing test, spawn a Cursor cloud agent for the fix on a new branch, run the full suite, open a draft PR. Never merge, never push to main, park anything touching auth, billing or migrations.
8. **Group chat.** Bots coordinate and hand off work. Give the group an objective, not a task list ("launch the October newsletter by Friday 5pm", name who owns what, "split the work yourselves, park anything public for me"). Teams can publish a shared bot for colleagues in the app or Slack.
9. **Draw the approval line by reversibility.**
   - Finish alone: draft, file, tag, summarize, research, prepare, reconcile.
   - Park for you: send to anyone outside, spend or move money or commit to a price, publish, delete non-junk, accept terms or sign up.
   - Unsure: if it can't be undone in under a minute, park and ask.
   - Target shape: "36 drafts queued, 0 sent."
10. **Watch cost, review weekly.** Bundled in plan, so no per-token billing; whether Opus jobs burn plan limits faster is unanswered. Watch usage for a week before adding routines. Weekly 15-minute review prompt: list each routine with fire count, output, skips/failures/guesses, unanswered parked items, and the least useful one; say if the model per step is unknown. Spot-check one output per routine by hand.

## Article's price comparison (API, not what you pay in the app)
Small job (30K in, 8K out) about 28 cents on Opus versus about 11 on Grok. Grok's rates double above 200K tokens; Opus stays flat.

## Takeaway
You decide what to delegate, where authority ends, and what data the bot may touch. Write all three into the charter.

## Related notes
- `grok-bot-one-person-company.md` (access and approval rules)
- `grok-bot-for-creators.md`
