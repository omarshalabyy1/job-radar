---
name: apply-autopilot
description: Use when filling or submitting a job application form for Omar (Workday, iCIMS, Taleo, Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee, a Typeform follow-up, apply by email), when answering a screening question, or when asked to autopilot applications.
---

> **System skills (MUST):** your next action after reading this skill is a Skill tool call to `superpowers:using-superpowers`, then the matching superpowers skill. CLAUDE.md rules win on conflict.

# Apply autopilot

Every application is one of a few patterns. Identify the pattern from the URL, fill it from the bank, stop where a human is needed.

## Never (CLAUDE.md, auto-apply plan)

- Never create an account, type a password, solve a CAPTCHA, take a test (HackerRank, HireVue, Codility) or tick a legal consent ("I certify", terms): those are Omar's. Stop on that page, tell him what is waiting.
- Never invent an answer. A question with no bank entry is parked (`core.apply_question`, `apply/answers.py: park`) and asked to Omar. Facts only from the master CV (fixed facts in the omar-brand skill's `references/cv-look.md` beat `core.cv`).
- Submit only inside the guardrails of `docs/superpowers/plans/2026-11-08-auto-apply.md` (score floor, daily cap, allow-list, kill switch, `apply.mode: live`). Otherwise stop at Review, show Omar the filled form, and wait for his yes.
- Not LinkedIn or Indeed forms: give him the link and the texts.

## The flow, any pattern

1. Pick the job and its draft folder (`output/applications/<day>/<company>-<title>/`: `cv` PDF, cover letter, `forms.md`).
2. Identify the pattern (table). 3. Fill each field from the bank (below), the draft's PDFs and `forms.md`. 4. Check every filled value against the CV. 5. At Review take a screenshot. 6. Submit (guardrails) or hand over. 7. When Omar says it is sent, or the receipt shows: `tracker.decide.decide(conn, job_id, "applied")`; the confirmation email reconciles later.

## Patterns

| Pattern (how to tell) | Steps | Autopilot does | Omar does |
|---|---|---|---|
| **Workday** (`myworkdayjobs.com`): Create Account/Sign In, Autofill with Resume, My Information, My Experience, Application Questions 1 of 2 and 2 of 2, Voluntary Disclosures, Review | one account per employer | Upload the CV PDF in Autofill; then fix what it parsed: delete duplicate jobs and schools, set titles and dates to the fixed facts; My Information (name, Cairo address, phone with Egypt +20, mobile); questions from the bank | The account (sign-in or creation), Voluntary Disclosures unless the bank holds his standing answer, the final submit |
| **iCIMS, Taleo, Oracle, SuccessFactors**: account first, many pages | like Workday | same as Workday | account, CAPTCHA |
| **Greenhouse** (`boards.greenhouse.io`, `job-boards.greenhouse.io`): one page | resume, cover letter, custom questions, EEO block | adapter `apply/adapters/greenhouse.py` | EEO unless banked |
| **Lever** (`jobs.lever.co/<co>/<id>`, then `/apply`): one page | resume, links, custom questions | adapter `lever.py` | hCaptcha |
| **Ashby** (`jobs.ashbyhq.com`), **Recruitee**, **SmartRecruiters**, **Workable** (`apply.workable.com`): one page or short steps, no account | resume, questions | adapters of the same names | CAPTCHA, a blocked page |
| **Follow-up form** (Typeform, Google Form, "complete our online form" in a confirmation mail) | questions the employer adds after applying | read the form, answer from the bank, park the rest | submit |
| **Apply by email** | to the posting's address, job title as subject, CV and letter PDFs attached | draft per the job-radar skill, step 5 | the send (his yes) |
| **Assessment** (HackerRank, Codility, HireVue, English or video exam) | timed | nothing; note the deadline in `core.deadline` | all of it |

## The bank

`output/applications/apply-answers.yaml` (gitignored): `- match: [phrases]` / `answer: text`, matched inside the question. Copy `apply-answers.example.yaml` to start. Sources, by question kind:

| Question | Answer from |
|---|---|
| Name, email, phone, address, links, employers, dates, education | master CV header and `cv-look.md` |
| Years of experience (total, per tool) | the CV: about 2 in total; a tool's years only if the CV or the bank says |
| Why this company, why this role, summary of me | written from the posting and the CV in his soft, appreciative voice; facts from the CV only |
| Salary expectation | the rule in `application-answers.md`; `python scripts/salaries.py <job_id>` for the market figure |
| Notice period, start date, relocation, remote, work authorization, visa or sponsorship, how did you hear | the bank only |
| Gender, ethnicity, veteran, disability (Voluntary Disclosures, EEO) | the bank only; none there: park |
| Anything new | park, ask Omar, then append the entry to the bank so it is never asked twice |

When Omar answers a parked question, add the entry in the same turn, with the shortest `match` phrase that identifies the question (not the whole sentence). Never put a secret, password or full ID number in the bank.
