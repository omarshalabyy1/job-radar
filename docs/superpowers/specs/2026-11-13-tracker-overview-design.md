# Tracker Overview page (2026-11-13)

## Why
Omar finds the tracker hard to follow: 13 pages, and applications split across two tables with different counts
(your statuses in `core.application`, the mailbox in `core.application_email`). He wants one place that answers:
how many jobs can I apply to, how many did I apply to, and how many are waiting, assessments, interviews, rejections
and replies.

## Approaches
- **A. New Overview page as the home page (picked by Omar).** One row of counts, then one applications table (one row
  per company) filtered by stage. The Applications and Today pages lose the sections the Overview replaces.
- B. Counts row only, added to the top of Today and Applications. Quick, but the duplicate tables and the page count stay.
- C. A drag-and-drop pipeline board. Too much for what was asked.

## Definitions (one helper, `tracker/overview.py`, feeds both the counts and the table)
- **Unit:** a company (`job_radar.applications.name_key`; an email that names no company counts by its title), as the
  Applications page and the funnel already count.
- **An application:** a company with an "applied" email, or one of your statuses applied / interview / offer /
  rejected, or an interview / offer / hired email (a job that came to you).
- **Stage (current, so the counts add up: Applied = Waiting + Viewed + Assessment + Interview + Rejected + Offer):**
  offer or hired → **Offer**; otherwise a rejection newer than any interview or assessment → **Rejected**; otherwise
  **Interview**, then **Assessment**, then **Viewed**; otherwise **Waiting**. Your own status counts the same as an email.
  A rejection or a view from before your latest application at that company (you applied again) no longer counts; an
  interview still does (it may be for another job there).
- **Assessment (by keyword):** an "interview" email whose subject or Inbox action says assessment, test, exam,
  HackerRank, Codility, video application, task or challenge. The mail reader files these as interviews today; the
  keywords split them out. No table change, no re-reading the mail.
- **Responses:** Applied minus Waiting. An automated "viewed" email counts as a response and also has its own count.
- **Can apply:** open jobs (new or saved) that no rule hides, plus how many of them were posted in the last 48 hours.
  The sidebar filters do not apply to the Overview.
- **Range switch:** Last 30 days / 90 days / All, by the company's latest activity (default 30 days).
- **Board:** the apply attempt's board when it names one, else the job's source (LinkedIn, Indeed, Wuzzuf, Ashby,
  Workable, Naukrigulf …), else where the first confirmation email came from (`applications.channel`). Blank rather
  than guessed.

## Live numbers (warehouse, 2026-11-13, under these definitions)
| Range | Applied | Waiting | Viewed | Assessment | Interview | Rejected | Offer | Responses |
|---|---|---|---|---|---|---|---|---|
| 30 days | 89 | 78 | 1 | 1 | 3 | 6 | 0 | 11 |
| 90 days | 122 | 105 | 2 | 1 | 4 | 9 | 1 | 17 |
| All | 215 | 191 | 2 | 2 | 6 | 13 | 1 | 24 |

Can apply: 190 jobs (the page, 2026-11-13 evening).

## The page
```
Overview                                         [Last 30 days | 90 days | All]
[Can apply 190] [Applied 89] [Responses 11]
[Waiting 78] [Viewed 1] [Assessment 1] [Interview 3] [Rejected 6] [Offer 0]
Show: (All | Waiting | Viewed | Assessment | Interview | Rejected | Offer)
Company | Job | Board | Stage | Applied on | Latest news (date) | Latest email (subject) | Link
```
Interviews and assessments sort to the top of the table, then the newest activity.

## What moves
- Navigation: **Overview** is the default page. Today keeps Apply next, Deadlines and the pipeline health; its
  "Your applications" funnel goes (the Overview replaces it).
- Applications page becomes **Follow-ups**: Follow up, Follow-up replies, Drafts. "Applied by you", the counters and
  the mailbox Applications table go (the Overview replaces them). Funnel and Company intelligence move to the
  Insights ("Interview rate") page unchanged.
- Kept names other code imports: `STAGE_EMAILS`, `STAGE_LABELS`, `follow_ups`.

## Not in scope
Egypt/Remote and role-family filters, schema changes, Neon, `decide()` (Applied / Noise / Skip stays as is),
re-classifying mail.

## Checks
New tests in `tests/test_overview.py` for the stage rules, the assessment keywords, the range switch and the board
precedence. `bash check.sh` prints `GATE: PASS`. The live tracker mounts the main checkout, so the branch is checked on
a second tracker started from this worktree on port 8502; `docker restart job-radar-tracker-1` shows it after the merge.
