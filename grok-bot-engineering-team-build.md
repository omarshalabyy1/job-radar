# Build an engineering team with Grok Bot (Piper, Bay, Fin, Tess + task board on disk)

Source: pasted article. It promotes InsForge (an open-source Apache 2.0 agent-operable backend), so read its "why InsForge" argument as a pitch. Product details (shared persistent computer, no model picker, 50 Bots/groups per account, InsForge CLI commands) are the article's, unchecked.

## Gaps in the article
- It says it builds "five skills that enforce the handoff discipline" and a `handoff-briefing` skill plus `templates/handoff.md`, but never shows their contents. Only `templates/task.md` is given. You must write the handoff template and skill yourself (spec below).
- Project paths are inconsistent: the board setup uses `auth-revamp`, the build uses `readlist`. Pick one name.
- The "ships in a few minutes" result is one demo run.

## Core idea
Multi-agent setups fail at the handoff (state stays in the sender's thread) or, if you use one agent, at context growth. Fix: **state lives in files on the shared computer; messages carry only a path.** Handoff quality is a file-format problem, not a prompting problem.

## Why it works on Grok Bot (per the article)
- All Bots on an account share one persistent computer; a file one Bot writes is readable by the next (use `/workspace`, project folders, descriptive names).
- Each Bot has its own screen, so they run in parallel, but one Bot runs one computer-use task at a time.
- Team rules (Cursor dashboard) stay in every Bot's context permanently, giving all Bots a shared engineering standard.
- No model picker: specialization comes from role, context and skills.

## Roster (keep to four)
| Bot | Owns | Never |
|---|---|---|
| Piper (PM) | Board, sequencing, handoff review | writes application code; approves own work |
| Bay (backend) | APIs, data model, migrations, server tests, InsForge | touches UI code |
| Fin (frontend) | UI, client state, integration | changes a server contract; invents an endpoint |
| Tess (tester) | Test plans, verification, bug files | fixes bugs she finds |

**Only Piper writes `board.md`.** Others write only their own task file and handoff note. Reason: parallel writers to one file silently lose edits. Each extra Bot adds a handoff and a failure point; add a fifth only for a stable owner with a real deliverable.

## Shared rules in every profile
Read `/workspace/projects/<project>/context/` first. Only Piper writes `board.md`. Never edit a task file you don't own. Nothing moves to review without a handoff note. Work on a branch, never push to main. Never run a destructive database command without asking.

## Profile-creation tip
Press Cmd/Ctrl+N, send the profile message, and ask the Bot to read back the saved name, title and description. If it's shorter than what you sent, it summarized; use Bot actions, Edit Profile, and paste directly.

## Role highlights
- **Piper:** decompose into 4-8 tasks with one owner, one deliverable, explicit dependencies. Reject handoff notes missing file paths, interface contract, or the unfinished list. Bring production data, credentials, deployment and scope changes to the human.
- **Bay:** use `npx @insforge/cli` (never global) for tables, buckets, edge functions, secrets; run `npx @insforge/cli metadata` before every task so decisions come from current state; handoff note must include exact file paths, full interface contract, every unspecified decision, and everything not implemented. Ask before migrations on anything but local or test databases.
- **Fin:** read task file, context, and the backend handoff note. If the contract is missing or ambiguous, stop and ask Piper. Needed endpoint missing means a blocked task.
- **Tess:** write the test plan from acceptance criteria, not the implementation. One defect per file (repro steps, expected, actual, screenshot/log), assigned to the owning layer's Bot. Test accounts only, never production customer data.

## Setup steps
1. **Create the four Bots** (profiles as above).
2. **Connect Bay to InsForge.** Create an InsForge account/project (or self-host with Docker Compose). Run `npx @insforge/cli login` once on the shared computer (Bay hands you the browser for sign-in). Then `npx @insforge/cli create` or `npx @insforge/cli link --project-id <id>`, `npx skills add insforge/insforge-skills` (installs `insforge`, `insforge-cli`, `insforge-debug`, `insforge-integrations`), and `npm install @insforge/sdk`. The CLI session belongs to the account, so every Bot inherits it: keep production projects on a different account from experiments.
3. **Build the board.** Ask Piper to create:
   ```text
   /workspace/projects/<project>/
     board.md   (sections: Backlog, Ready, In Progress, Blocked, Review)
     context/   architecture.md  conventions.md  glossary.md
     tasks/  handoffs/  artifacts/  templates/
   ```
   Fill `context/` yourself (stack and auth; naming, error format, tests, branch naming, definition of done; domain terms). Highest-leverage 20 minutes.
4. **Task file template** (`templates/task.md`):
   ```markdown
   # T-000 Title
   owner:
   status: ready
   depends_on: []
   blocks: []
   created:
   updated:

   ## Goal
   One paragraph on what done looks like from the user's point of view.

   ## Acceptance criteria
   - [ ] Criterion written so it can be verified without reading the code

   ## Context to read first
   - context/architecture.md
   - context/conventions.md

   ## Notes
   ```
5. **Handoff template** (`templates/handoff.md`): not in the article. Minimum from its rules: exact file paths, full interface contract (including every error code), unspecified decisions made, what was not implemented, what was verified, unfinished list.

## Demo project ("readlist")
Signed-in user saves links, tags them, marks read, sees only their own. Six group-chat messages:
1. Founding message: roles; work in direct chats; use the group only for handoffs, cross-layer blockers, direct mentions; don't acknowledge messages not addressed to you.
2. `@Bay`: authenticate CLI, create project, email/password auth, `links` and `tags` tables, row-level security, run `metadata` and paste output. No password reset yet, no production deploy.
3. `@Fin`: React + Vite + TypeScript + `@insforge/sdk`; three screens (auth, list, add form); ask Bay for table shapes; don't change the backend.
4. `@Piper`: write `context/` from what was actually built; ask Bay and Fin to confirm; don't invent details.
5. `@Piper`: create board, tasks folder, handoffs folder, both templates.
6. `@Piper`: feature "password reset"; read context, break into tasks, fill dependencies both sides, show the plan before writing files. After approval run it alone; check each handoff against `templates/handoff.md`; come back only for scope change, blocked question, deployment, or production.

Run result per the article: four tasks; Bay found the requested 30-minute expiry and session revocation weren't project config, did not guess, put the question to Piper; Piper blocked the task and asked the human; scope change recorded; Bay recorded actual platform defaults; Fin and Tess followed; Tess stated in her note what she did not test.

## Biggest failure mode: reading the backend wrongly
A wrong backend belief becomes a stated fact in a handoff note, Fin builds on it, Tess tests it. The article's argument for InsForge: one `metadata` call returns tables, policies, auth config and functions in a structured view; docs retrieval is scoped; errors are structured (e.g. RLS denial distinguishable from other 403s). With four Bots each carrying role, task, context and handoff note, context cost matters.

## Model
Board holds state, handoff notes are transitions, Bots read before they act. A Bot's handoff is a task ID and a path, never the content.

## Related notes
- `grok-bot-first-agent-team.md`, `grok-bot-for-engineering.md`, `grok-bot-eight-bot-team.md`
