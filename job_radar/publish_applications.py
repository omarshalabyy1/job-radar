"""publish_applications: your application emails, and the stage they put each job in, to Neon (the website's Applications page).

track_applications reads Gmail into core.application_email; this copies it, with no other change to Neon's schema:
- tracker.application_email: every email but kind 'other' (Neon's table has no such kind), once by message_id. The job it is
  about goes as its Neon id (nat_id of its job_key, as publish does), or empty when that job is not on Neon; a job found
  later fills an empty one, nothing else of a stored row changes.
- app.job_status of the owner: for each job with an email, the highest stage on STAGES ('hired' counts as offer, 'viewed'
  moves nothing). A job with no row gets one; a row moves up only, so a later stage you set by hand stays and 'ignored' is
  never touched (an unchanged row is not written again). applied_at, interview_at and offer_at are the dates of the first such email. The status trail
  (app.status_event) records the email's sender and subject as the source.
The owner is app.app_user.is_owner, else OWNER_EMAIL. Nothing is pruned."""

from __future__ import annotations

import os

import psycopg

from .publish import keys_by_id, nat_id

STAGES = ["saved", "applied", "interview", "rejected", "offer"]  # applications.LADDER without 'new'
MOVES = {"applied": "applied", "interview": "interview", "rejected": "rejected", "offer": "offer", "hired": "offer"}
EMAIL = ("message_id", "received_at", "sender", "subject", "company", "title", "kind", "job_id")


def stages(rows: list[tuple], on_neon: set) -> dict:
    """{Neon job id: (stage, {stage: date of its first email}, subject, sender)} from rows of EMAIL, for the jobs on Neon."""
    best = {}
    for message_id, received_at, sender, subject, _, _, kind, job_id in sorted(rows, key=lambda r: r[1]):
        stage = MOVES.get(kind)
        if not stage or job_id not in on_neon:
            continue
        current = best.setdefault(job_id, [stage, {}, subject, sender])
        current[1].setdefault(stage, received_at)
        if STAGES.index(stage) >= STAGES.index(current[0]):
            current[0], current[2], current[3] = stage, subject, sender
    return best


def publish_applications(conn) -> None:
    url = os.environ.get("NEON_DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("publish_applications: no NEON_DATABASE_URL in .env; nothing was published")
    rows = conn.execute(f"SELECT {', '.join(EMAIL)} FROM core.application_email WHERE kind <> 'other' ORDER BY received_at").fetchall()
    keys = keys_by_id(conn)
    rows = [(*r[:7], nat_id(keys[r[7]]) if r[7] in keys else None) for r in rows]
    try:
        neon = psycopg.connect(url, connect_timeout=30)  # Neon wakes slowly
    except psycopg.Error:
        raise RuntimeError("publish_applications: cannot connect to Neon; check NEON_DATABASE_URL") from None
    with neon, neon.cursor() as cur:
        on_neon = {j for (j,) in cur.execute("SELECT job_id FROM jobs.job WHERE job_id = ANY(%s)",
                                             ([r[7] for r in rows if r[7]],)).fetchall()}
        rows = [(*r[:7], r[7] if r[7] in on_neon else None) for r in rows]
        cur.executemany(
            f"INSERT INTO tracker.application_email AS t ({', '.join(EMAIL)}) VALUES ({', '.join(['%s'] * len(EMAIL))})"
            " ON CONFLICT (message_id) DO UPDATE SET job_id = excluded.job_id WHERE t.job_id IS NULL AND excluded.job_id IS NOT NULL", rows)
        owner = cur.execute("SELECT user_id FROM app.app_user WHERE is_owner").fetchone()
        if not owner and os.environ.get("OWNER_EMAIL", "").strip():
            owner = cur.execute("SELECT user_id FROM app.app_user WHERE lower(email) = lower(%s) ORDER BY created_at LIMIT 1",
                                (os.environ["OWNER_EMAIL"].strip(),)).fetchone()
        moved = 0
        for job_id, (stage, first, subject, sender) in stages(rows, on_neon).items() if owner else ():
            cur.execute("SELECT set_config('app.source', 'email', true), set_config('app.source_subject', %s, true),"
                        " set_config('app.source_sender', %s, true)", (subject, sender))
            cur.execute(
                "INSERT INTO app.job_status AS t (user_id, job_id, status, applied_at, interview_at, offer_at)"
                " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (user_id, job_id) DO UPDATE SET"
                " status = CASE WHEN array_position(%s::text[], t.status) < array_position(%s::text[], excluded.status)"
                "               THEN excluded.status ELSE t.status END,"
                " applied_at = coalesce(t.applied_at, excluded.applied_at), interview_at = coalesce(t.interview_at, excluded.interview_at),"
                " offer_at = coalesce(t.offer_at, excluded.offer_at)"
                " WHERE array_position(%s::text[], t.status) < array_position(%s::text[], excluded.status)"
                " OR (t.applied_at IS NULL AND excluded.applied_at IS NOT NULL) OR (t.interview_at IS NULL AND excluded.interview_at IS NOT NULL)"
                " OR (t.offer_at IS NULL AND excluded.offer_at IS NOT NULL)",
                (owner[0], job_id, stage, first.get("applied"), first.get("interview"), first.get("offer"), STAGES, STAGES, STAGES, STAGES))
            moved += cur.rowcount
    print(f"publish_applications: {len(rows)} emails read, {sum(r[7] is not None for r in rows)} on a job on Neon, "
          f"{moved} job statuses written (new or moved up)")
