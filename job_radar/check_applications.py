"""check_applications: hourly, after track_applications. Links each application email to the job you applied to with the
same company and title (job_radar/overview.py reads them so), then moves that job's status up from it. Only emails linked
to no job are touched, so a link or a status you corrected by hand is never redone, and a rerun changes nothing.

An application your mailbox confirms that the tracker has no job for (you applied on a site the radar never listed) gets
its job (source 'applied', never in_scope: it is no job to find, grade or apply to) and status here, from what the email itself says: its company, its title and, for LinkedIn, the posting's link and
place. One whose emails name no title is left out: nothing is invented."""

import re

from .applications import LADDER, move_status
from .config import ROLE_LABEL, place_of, role_of
from .overview import MAIL, STATUSES, applications

STATUS = {"waiting": "applied", "assessment": "interview", "interview": "interview", "rejected": "rejected", "offer": "offer"}


def linkedin_posting(body: str, company: str) -> tuple[str, str, str] | None:
    """(title, location, link) of the job in LinkedIn's "your application was sent to <company>" email: after the posting's
    link come the title, then "<company> · <place>"."""
    m = re.search(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)[^)]*\)\s+(.+?)\s+" + re.escape(company) + r" · (.+?) \(https", body or "")
    return (m.group(2), m.group(3), f"https://www.linkedin.com/jobs/view/{m.group(1)}/") if m else None


def add_job(cur, app: dict) -> int | None:
    """The tracker job of an application known from emails alone, made (or found by its key) with your status for its
    stage; None when no email of it names a title, or the job already has a status of yours."""
    from .steps import job_key  # steps imports this module

    # with no posting link, the job's link is its confirmation email, without the mailbox: publish sends job_url to Neon
    title, location, url = app["job"], "", re.sub(r"\?authuser=[^#]*", "", app["email_link"])
    if not title:
        for message_id, mailbox in app["emails"]:
            body = cur.execute("SELECT body FROM core.mail WHERE message_id = %s AND mailbox = %s", (message_id, mailbox)).fetchone()
            if body and (found := linkedin_posting(body[0], app["company"])):
                title, location, url = found
                break
        else:
            return None
    key, rank = job_key(title, app["company"], url), role_of(title)
    row = cur.execute("SELECT job_id FROM core.job WHERE job_key = %s", (key,)).fetchone()
    job_id = row[0] if row else cur.execute(
        "INSERT INTO core.job (job_key, title, company, location, place, source, job_url, role_rank, role, target_company,"
        " first_seen, in_scope) VALUES (%s, %s, %s, %s, %s, 'applied', %s, %s, %s, false, %s, false) RETURNING job_id",
        (key, title, app["company"], location, place_of(location) or "Unknown location", url, rank or 0,
         ROLE_LABEL.get(rank, "Other"), app["applied_on"].date())).fetchone()[0]
    cur.execute("INSERT INTO core.application (job_id, status, note, updated_at) VALUES (%s, %s, %s, %s) ON CONFLICT (job_id) DO NOTHING",
                (job_id, STATUS[app["stage"]], f"confirmed by email {app['applied_on']:%Y-%m-%d} (check_applications)", app["latest_at"]))
    if not cur.rowcount:  # the job has your own status already (one you ignored): it stays yours
        return None
    cur.execute("UPDATE core.application_email SET job_id = %s WHERE message_id = ANY(%s) AND job_id IS NULL",
                (job_id, [message_id for message_id, _ in app["emails"]]))
    return job_id


def check_applications(conn) -> None:
    apps = applications(conn.execute(MAIL).fetchall(), conn.execute(STATUSES).fetchall())
    linked = moved = added = 0
    with conn.cursor() as cur:
        for app in apps:
            for message_id, kind, received_at in sorted(app["to_link"], key=lambda m: m[2]):  # oldest first: the status climbs in order
                cur.execute("UPDATE core.application_email SET job_id = %s WHERE message_id = %s AND job_id IS NULL",
                            (app["job_id"], message_id))
                linked += cur.rowcount
                status = "offer" if kind == "hired" else kind  # a job's status stops at offer
                if cur.rowcount and status in LADDER:
                    moved += move_status(cur, app["job_id"], status, f"from your mailbox {received_at:%Y-%m-%d} (check_applications)")
            if not app["in_tracker"] and app["company"] and app["applied_on"]:
                added += add_job(cur, app) is not None
    conn.commit()
    print(f"check_applications: {linked} emails linked to your applications, {moved} statuses moved up, {added} applications "
          f"known from emails alone added to the tracker; "
          f"{sum(a['in_tracker'] and not a['receipt'] for a in apps)} applications have no confirmation email, "
          f"{sum(not a['in_tracker'] for a in apps) - added} confirmed by email are not in the tracker (no title or company in the email)")
