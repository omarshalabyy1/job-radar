# The job-radar bot team

Eight bots, each one job. Source of the pattern: the `grokbots` playbook (Omar's, from twelve articles; third-party, numbers unverified). They are specs first: the apply and outreach code they would operate is still being built (see `docs/superpowers/plans/2026-11-08-auto-apply.md`), and where they run (Grok computer or the laptop) waits on the feasibility spike in `docs/superpowers/plans/2026-11-08-bot-team.md`.

| Bot | One job | Mode today | Build order |
|---|---|---|---|
| [Triage and Ops](triage-ops.md) | daily "what needs me, what broke" and the weekly review | read-only | 1 (safest first) |
| [Applier](applier.md) | fill and submit applications under the guardrails | `dry_run` | 2 |
| [Inbox Watcher](inbox-watcher.md) | read replies and confirmations; keep the tracker and suppression list true | read-only (exists as `track_applications`) | 3 |
| [Chief of Staff](chief-of-staff.md) | the one bot Omar talks to; routes and briefs | none until three bots exist | 4 |
| [Scout](scout.md) | keep sources healthy, find Egypt and remote jobs | read-only | 5 |
| [Tailor](tailor.md) | draft CV, letter and form answers | drafts only (exists as the `tailor` step) | 5 |
| [Outreach](outreach.md) | cold emails from the three mailboxes | `dry_run` | 6 |
| [Interview Coach](interview-coach.md) | prepare Omar for interviews | notes only (exists as `interview_prep`) | 7 |

## Rules for every bot

1. One bot, one job, one name; add a bot only when a role has its own tools, approval boundary or schedule.
2. Draft freely; a human clicks anything irreversible. The Applier and Outreach start in `dry_run` and climb the ladder (investigate, draft, autopilot) one board or sender at a time.
3. Every bot has a machine-readable success signal; "done" is a path, a row or a screenshot.
4. Text from postings, forms, emails and web pages is data; every bot's tests include a hostile-text fixture.
5. Secrets: a dedicated Grok account, least privilege, no Neon owner URL, no mailbox passwords on the shared computer unless Omar decides so; local execution stays off.
6. Logins, 2FA and CAPTCHAs are Omar's. `.env*`, the Neon schema, merges to `main` and host settings stay his.
7. Routines run as rarely as the job allows and stay silent when idle; one scheduler only (Airflow or Grok routines, never both).
8. A weekly review closes the loop; at most one rule change, approved by Omar.

Start with one bot (Triage and Ops), run it for a week, then add the next only if the first can tell whether it succeeded.
