"""profile_once: one-time fill of Omar's empty profile on the website, from his CVs in the warehouse.

Run: python -m job_radar.profile_once            (dry run: reads both databases, writes nothing)
     python -m job_radar.profile_once --write    (writes app.user_profile, then app.refresh_scores())
Grain: app.user_profile is one row per user (user_id), the user being the app.app_user with is_owner = true. The
skills are the 50 that appear on the most CVs (core.cv_skill, one row per CV label per skill; ties by name), the
cv_text is the text of the newest CV (core.cv, one row per label). job_tracks, gig_tracks and locations stay empty,
which the site reads as "all".
Never overwrites his edits: it writes only when the row is missing or empty (no skills, no cv_text, no tracks, no
locations), and the upsert's WHERE re-checks that in the same statement, so a save he makes meanwhile wins. A second
--write finds the profile set, prints so and writes nothing. One transaction on Neon; the dry run's is read-only.
Neon's url is NEON_DATABASE_URL, never printed; empty, it prints a line and exits 1."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from .db import connect

UPSERT = ("INSERT INTO app.user_profile AS p (user_id, skills, cv_text) VALUES (%s, %s, %s)"
          " ON CONFLICT (user_id) DO UPDATE SET skills = excluded.skills, cv_text = excluded.cv_text, updated_at = now()"
          " WHERE p.skills = '{}' AND p.cv_text IS NULL AND p.job_tracks = '{}' AND p.gig_tracks = '{}' AND p.locations = '{}'")


def main() -> int:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    write = sys.argv[1:] == ["--write"]
    if sys.argv[1:] and not write:
        raise SystemExit(__doc__)
    url = os.environ.get("NEON_DATABASE_URL", "").strip()
    if not url:
        print("profile_once: no NEON_DATABASE_URL in .env")
        return 1
    with connect() as conn:
        skills = [r[0] for r in conn.execute("SELECT skill FROM core.cv_skill GROUP BY skill ORDER BY count(*) DESC, skill LIMIT 50")]
        label, cv_text = conn.execute("SELECT label, text FROM core.cv ORDER BY uploaded_at DESC LIMIT 1").fetchone()
    assert len(skills) <= 50 and len("".join(skills)) <= 1000, "skills break the site's limits"
    assert 0 < len(cv_text) <= 100000, "cv_text is empty or over the site's limit"
    try:
        neon = psycopg.connect(url, connect_timeout=30)  # Neon wakes slowly
    except psycopg.Error:
        raise RuntimeError("profile_once: cannot connect to Neon; check NEON_DATABASE_URL") from None  # the error can quote the url
    neon.read_only = not write
    with neon:
        owner = neon.execute("SELECT user_id FROM app.app_user WHERE is_owner").fetchone()
        if not owner:
            print("no owner row; sign in first")
            return 1
        row = neon.execute("SELECT cardinality(skills), cv_text IS NULL, cardinality(job_tracks) + cardinality(gig_tracks) + cardinality(locations)"
                           " FROM app.user_profile WHERE user_id = %s", (owner[0],)).fetchone()
        print(f"owner found: yes; profile row: {'missing' if row is None else f'{row[0]} skills, cv_text null {row[1]}, {row[2]} track/location values'}")
        print(f"cv: '{label}', {len(cv_text)} characters; skills ({len(skills)}): {', '.join(skills)}")
        if row is not None and row != (0, True, 0):
            print("profile already set on the website; not overwritten")
            return 0
        if not write:
            print("dry run: nothing written; add --write to write")
            return 0
        written = neon.execute(UPSERT, (owner[0], skills, cv_text)).rowcount
        neon.execute("SELECT app.refresh_scores()")
        matches = neon.execute("SELECT count(*) FROM app.job_match WHERE user_id = %s", (owner[0],)).fetchone()[0]
        top = neon.execute("SELECT j.job_id, j.title, j.company, m.score FROM app.job_match m JOIN jobs.job j USING (job_id)"
                           " WHERE m.user_id = %s ORDER BY m.score DESC, j.job_id LIMIT 5", (owner[0],)).fetchall()
    print(f"profile rows written: {written}; app.job_match rows for the owner: {matches}")
    for job_id, title, company, score in top:
        print(f"  {job_id}  {score}  {title} - {company}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
