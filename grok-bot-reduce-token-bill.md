# 12 ways to reduce your Grok Bot token bill

Source: pasted article by @maestrooth. The author's headline claim (99% of a weekly allowance on the $200 Ultra plan in three days, 84% of tokens spent re-reading the chat) is their own experience. Plan details, menu paths and the Cursor-pool behavior are the article's and unchecked. Menu paths differ across the notes (this one says Settings, Bot, Custom Rules; others say Settings, General, Auto-review or custom rules), so check the app.

## How usage works (per the article)
- Weekly grant that resets weekly; when gone the bot stops, unless on-demand usage (legacy Cursor plans) or prepaid credit (The X Subscription) is on.
- Counted in agent steps and tokens: a one-line request that sends the bot through ten websites costs more than a long message it can answer directly.
- No model picker. What you control: how often it runs, how much it reads, what it can touch.
- Main drains (from a Cursor forum thread): tools, browser use, subagents, and re-reading the whole conversation every turn.

## The 12 settings
1. **One conversation per task.** Every turn re-reads the whole chat. Start fresh with +; old chats cost nothing sitting there. Handoff prompt: write a note in under 12 lines (task, done, left in order, files/links/accounts, decisions made), skip what the next chat can find on its own.
2. **Wider routine schedules (the big one).** Every run uses usage; every 15 min = 96 runs/day, hourly = 24, three times a day = 3. Ask the owning bot to list each routine's saved "When to run" field, runs per day and what it must catch, then propose the widest schedule that still catches it; change nothing until you approve each. Then confirm the saved field yourself.
3. **Narrow event triggers.** A Slack trigger on "any message" in all of Slack fires on everything. Ask the bot to list routines that start from Slack, email or webhook, show what each watches, and rewrite each to one channel and one exact phrase or mention, showing before and after and waiting for your OK.
4. **Pause what you don't use.** Ask which routines produced nothing useful in 7 days (with their last 3 runs); pause the ones you mark, don't delete.
5. **A dedicated bot for routines.** Routines belong to one bot; if they live on your daily chat bot, every run piles into a long conversation. Keep a separate "Routines" bot.
6. **Small, safe test runs.** The Test button does real work (can change files, browse, use connected apps, uses usage). Ask for the smallest safe input (one email, one ticket, one page, one row; nothing that sends, changes files you care about, or posts) and what the test should produce.
7. **Turn off app tools the bot doesn't need.** Connect apps in the sidebar lists tools per app; each can be toggled. A bot with browser tools off can't spend twenty steps clicking.
8. **Custom Rules that ask before expensive actions** (Settings, Bot, Custom Rules; Ask first wins on conflict; sync across desktops and iPhone):
   ```text
   Ask me first before you:
   - open more than 5 web pages for one task
   - start a subagent
   - re-run a step that already failed twice
   - start any job you expect to take more than 15 minutes
   When you ask, tell me in one line what you want to do and why.
   ```
   Run one small task afterward to confirm it actually stops and asks.
9. **Hand heavy coding to Cursor cloud agents** (legacy Cursor plans only). Per Cursor support quoted in the forum thread, the bot's part comes from the Grok Bot pool and the agent's part from your separate Cursor allowance. On The X Subscription it's one shared pool, so no saving.
   ```text
   For coding work, act as the coordinator. Write the task as a short spec,
   hand the implementation to a Cursor cloud agent, and only review the result.
   Report back with the diff summary and test result.
   ```
10. **A spending cap.** Legacy Cursor: cursor.com/dashboard, Spending, Monthly Limit (or turn on-demand off). The X Subscription: grok.com/dashboard/billing, leave Auto Top-up off or set a monthly additional-usage limit. The cap won't stop a run already in progress; it can finish a bit past the limit.
11. **Check the meter before big jobs** (Settings, Usage & Billing). Weekly on reset day, ask the bot which 5 tasks or routines used the most usage and why, with one cut for each, applying nothing yet.
12. **Tight task briefs with a stop condition:**
    ```text
    Task: [one sentence]
    Done means: [the exact output I will check]
    Use only: [apps, folders or sites allowed]
    Stop and ask me if:
    - you need anything outside that list
    - the same step fails twice
    - you are past [N] steps and not close to done
    Give me the result first, then a 3-line note on what you skipped.
    ```

## Common mistakes
Testing on real data; raising the cap before fixing routines (docs say widen schedules and narrow triggers first); expecting the cap to stop a running task (put long jobs behind a Custom Rule); delegating to Cursor agents on The X Subscription (same pool); keeping one chat forever.

## 10-minute setup order
Check Usage & Billing; run the Setting 2 prompt and approve wider schedules; Setting 3 prompt; Setting 4 prompt; add the Setting 8 rules; turn off unused tools; set the spending cap; save the Setting 12 brief. Then: new chat per task and a usage check on reset day.

## Related notes
`grok-bot-72-hour-field-notes.md` (routine leaks, once-a-day rule), `grok-bot-masterclass.md` (limits, Auto review)
