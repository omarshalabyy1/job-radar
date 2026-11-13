"""check_applications: hourly, after track_applications. Links each application email to the job you applied to with the
same company and title (job_radar/overview.py reads them so), then moves that job's status up from it. Only emails linked
to no job are touched, so a link or a status you corrected by hand is never redone, and a rerun changes nothing."""

from .applications import LADDER, move_status
from .overview import MAIL, STATUSES, applications


def check_applications(conn) -> None:
    apps = applications(conn.execute(MAIL).fetchall(), conn.execute(STATUSES).fetchall())
    linked = moved = 0
    with conn.cursor() as cur:
        for app in apps:
            for message_id, kind, received_at in sorted(app["to_link"], key=lambda m: m[2]):  # oldest first: the status climbs in order
                cur.execute("UPDATE core.application_email SET job_id = %s WHERE message_id = %s AND job_id IS NULL",
                            (app["job_id"], message_id))
                linked += cur.rowcount
                status = "offer" if kind == "hired" else kind  # a job's status stops at offer
                if cur.rowcount and status in LADDER:
                    moved += move_status(cur, app["job_id"], status, f"from your mailbox {received_at:%Y-%m-%d} (check_applications)")
    conn.commit()
    print(f"check_applications: {linked} emails linked to your applications, {moved} statuses moved up; "
          f"{sum(a['in_tracker'] and not a['receipt'] for a in apps)} applications have no confirmation email, "
          f"{sum(not a['in_tracker'] for a in apps)} confirmed by email are not in the tracker")
