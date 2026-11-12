"""What the Applied and Noise ticks do (Jobs and Today pages): one click sets a job's status and note in core.application."""

from job_radar.applications import cairo_today

# the noise reasons, by button: the note says why you passed on it
NOISE = {"closed": "applications closed", "senior": "too many years asked", "junior": "intern or too junior"}


# the Decide column's choices in the Jobs and Today tables: label -> (action, noise reason)
DECISIONS = {"Applied": ("applied", None), "Noise: closed": ("noise", "closed"),
             "Noise: high exp.": ("noise", "senior"), "Noise: intern": ("noise", "junior"), "Skip": ("skip", None)}


def apply_decisions(conn, edited_rows: dict, job_ids: list[int]) -> int:
    """Save the Decide choices made in a table now: `edited_rows` is Streamlit's {row position: {column: value}} and
    `job_ids` the job of each row. A row edited in another column only is left to the Save button. Returns how many."""
    done = 0
    for position, changes in edited_rows.items():
        if changes.get("applied"):  # the Applied tick box: one click
            decide(conn, int(job_ids[int(position)]), "applied")
            done += 1
        elif changes.get("noise"):  # the Noise tick box: one click, the usual reason (applications closed)
            decide(conn, int(job_ids[int(position)]), "noise", "closed")
            done += 1
        elif changes.get("decide") in DECISIONS:
            decide(conn, int(job_ids[int(position)]), *DECISIONS[changes["decide"]])
            done += 1
    return done


def mark_deadlines_done(conn, edited_rows: dict, deadline_ids: list[int]) -> int:
    """Save the Done ticks made in the deadlines table now; returns how many deadlines were ticked."""
    ticked = [int(deadline_ids[int(position)]) for position, changes in edited_rows.items() if changes.get("done")]
    if ticked:
        conn.execute("UPDATE core.deadline SET done = true WHERE deadline_id = ANY(%s)", (ticked,))
    return len(ticked)


def decide(conn, job_id: int, action: str, reason: str | None = None) -> None:
    """applied | noise (with a reason from NOISE) | skip. Applied keeps an earlier note (the draft's); noise and skip
    ignore the job, which the emails, the lists and the apply queue then leave out."""
    day = cairo_today()
    if action == "applied":
        status, note = "applied", f"applied by you {day}"
    elif action == "noise" and reason in NOISE:
        status, note = "ignored", f"noise: {NOISE[reason]} (Omar, {day})"
    elif action == "skip":
        status, note = "ignored", f"skipped by Omar {day}"
    else:
        raise ValueError(f"unknown action {action!r} / reason {reason!r}")
    conn.execute(
        "INSERT INTO core.application (job_id, status, note) VALUES (%s, %s, %s)"
        " ON CONFLICT (job_id) DO UPDATE SET status = EXCLUDED.status, updated_at = now(),"
        " note = CASE WHEN EXCLUDED.status = 'applied' THEN concat_ws('; ', core.application.note, EXCLUDED.note)"
        " ELSE EXCLUDED.note END", (job_id, status, note))
