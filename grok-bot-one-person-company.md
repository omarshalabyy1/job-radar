# Run a one-person company on Grok Bot: the 7-day setup

Source: pasted article. Prices, usage limits and approval-rule behavior are the article's claims, not checked against xAI's current docs (Grok Bot is a beta).

## What a Bot is
Named, persistent AI teammate with a role. Runs on a cloud computer (browser, terminal, filesystem), keeps working when your laptop is off. Supports scheduled routines, reusable skills, connectors (Gmail, Calendar, Drive, Notion, GitHub), and group chats of 2-6 Bots. Included in X Premium ($30/month per the article). Usage is a weekly pool shared with Grok and Cursor; exact numbers are not published.

## Three rules
1. **Separate accounts, minimum access.** Bots share one computer, so treat whatever one can reach as reachable by all. Dedicated Bot email. Limited seats in CRM and help desk. Read access by default, write only where the job needs it. No banking logins, card details or payment admin.
2. **Anything irreversible needs your click.** Sending, publishing, refunding, deleting, signing, paying. Bots prepare, you approve. "Require Approval" rules override "Always Allow".
3. **Your knowledge lives in files you own.** Bot memory can't be inspected or exported item by item, so keep business knowledge in Drive, Notion or GitHub and connect the Bots to it.

## Handbook (30 minutes)
```text
handbook/
  company.md     what we sell, to whom, for how much
  voice.md       how we write, with 5 real examples
  never.md       claims, phrases and topics we avoid
  faq.md         every customer question + approved answer
  policies.md    refunds, response times, escalation
  customers.md   ideal customer, in detail
```
- Be concrete ("no exclamation marks, answer in the first sentence"), not vague ("be professional").
- Corrected a Bot twice for the same thing? Put the fix in the handbook.

## The six Bots
| Bot | Role | Schedule | Key rule |
|---|---|---|---|
| Brief | Chief of Staff | Weekdays 07:00 | One brief under 200 words: decisions first, meeting prep, numbers, anomalies. Report only. |
| Scout | Research and leads | Mon 06:00 competitors; weekdays 08:00 leads | Every claim sourced; unverified marked. Leads get a two-line summary and fit score. |
| Quill | Content | Fri 10:00 | One long-form piece becomes newsletter section, thread, 3 posts, short script. Drafts only. |
| Helm | Support | Weekdays 09:00 | Drafts from faq.md and policies.md. Refund, legal, angry customer, or no FAQ answer: escalation note. Never invents policy. |
| Penny | Ops and cash | 1st of month | Read-only exports. Totals computed with code and checked against source. No estimates. |
| Kit | Reviewer | After drafts | Checks Quill, Helm, Scout drafts against never.md, voice.md, policies.md. Verdict: "clean" or "broke rule X". |

## 7-day plan
1. Lock down: Premium, Bot email, limited seats, approval rules.
2. Write the six handbook files (rough is fine).
3. Hire Brief: run manually, then schedule.
4. Hire Helm: drafts only, count minutes saved.
5. Hire Kit: point at Helm's drafts, tune until it catches what you would.
6. Hire Quill and Scout: one routine each.
7. Hire Penny: run the month's books manually, check every number. Review the Usage page for costly routines.

Group chats last: multi-Bot workflows burn usage fast. Use them only for work that crosses roles.

## Your week
Approve the queue, do the work only you can do, review Friday's summary (Brief, Fri 16:00).

## Limits
- Beta: build processes that tolerate surprises.
- Shared computer is not a security boundary.
- Weekly usage pool can run out mid-task.
- CAPTCHAs and logins block browser automation; prefer connectors.
- Skills drafted from screen recordings are first drafts: review and test.

## Takeaway
Bots make labor cheap, not judgment. Write the handbook tonight, hire Brief tomorrow.
