"""embed: local Ollama vectors for the jobs and your CVs: how close each job is to your CV.

Each job still in the window (config.KEEP_DAYS), in any place, with a description (a title alone is a
weak signal) and no vector yet from settings.yaml embeddings.model gets one from its title, company and the
start of its description, into core.job_vector. Your CVs in core.cv are embedded when they have no vector from that model
yet or were uploaded after their vector was made (core.cv_vector.embedded_at; a CV uploaded again keeps its label), into
core.cv_vector. Then core.job.cv_similarity = how close the job is
to your closest CV (cosine, 0-1). The job_radar DAG runs it after match_skills. With features.embeddings false,
or Ollama not running or without the model, it prints why and stops green (a WARNING line when jobs or CVs were waiting)."""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from .config import FEATURES, KEEP_DAYS, SETTINGS
from .sources import time_left

EMBED_SECONDS = 200  # the DAG gives the task 240 s; the jobs left are embedded next run
BATCH = 32  # texts per Ollama call


def cosine(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def vectors_of(texts: list) -> list:
    cfg = SETTINGS["embeddings"]
    # on the CPU (num_gpu 0): the model is small, and Ollama's GPU runner crashed on embeddings on the laptop's GPU
    body = {"model": cfg["model"], "input": texts, "options": {"num_gpu": 0}}
    request = urllib.request.Request(cfg["url"] + "/api/embed", headers={"Content-Type": "application/json"},
                                     data=json.dumps(body).encode())
    with urllib.request.urlopen(request, timeout=120) as r:
        return json.load(r)["embeddings"]


def stale_cvs(conn, model: str) -> list[tuple]:
    """(label, text) of the CVs to embed: none yet from this model, or uploaded after their vector was made."""
    return conn.execute(
        "SELECT c.label, c.text FROM core.cv c LEFT JOIN core.cv_vector v USING (label)"
        " WHERE v.embedded_at IS NULL OR v.model <> %s OR v.embedded_at < c.uploaded_at ORDER BY c.label", (model,)).fetchall()


def embed(conn) -> None:
    if not FEATURES["embeddings"]:
        print("embed: off in settings.yaml (features.embeddings)")
        return
    started, stamp = time.monotonic(), datetime.now(timezone.utc)  # stamp: taken before the CVs are read, so a CV uploaded while they are embedded is embedded again next run
    cfg = SETTINGS["embeddings"]
    model = cfg["model"]
    cvs = stale_cvs(conn, model)
    jobs = conn.execute(
        "SELECT j.job_id, j.title || ' at ' || j.company || E'\\n\\n' || left(j.description, 2000) FROM core.job j"
        " WHERE j.first_seen >= current_date - %s AND j.description IS NOT NULL"
        " AND NOT EXISTS (SELECT 1 FROM core.job_vector v WHERE v.job_id = j.job_id AND v.model = %s)"
        " ORDER BY j.job_id", (KEEP_DAYS, model)).fetchall()
    embedded = 0
    try:
        if cvs:
            cv_vectors = vectors_of([text for _, text in cvs])
            with conn.cursor() as cur:
                cur.executemany("INSERT INTO core.cv_vector (label, vector, model, embedded_at) VALUES (%s, %s, %s, %s)"
                                " ON CONFLICT (label) DO UPDATE SET vector = EXCLUDED.vector, model = EXCLUDED.model,"
                                " embedded_at = EXCLUDED.embedded_at",
                                [(label, v, model, stamp) for (label, _), v in zip(cvs, cv_vectors)])
            conn.commit()
        for i in range(0, len(jobs), BATCH):
            if time_left(EMBED_SECONDS) < 20:
                break
            batch = jobs[i:i + BATCH]
            with conn.cursor() as cur:
                cur.executemany("INSERT INTO core.job_vector (job_id, vector, model) VALUES (%s, %s, %s)"
                                " ON CONFLICT (job_id) DO UPDATE SET vector = EXCLUDED.vector, model = EXCLUDED.model",
                                [(job_id, v, model) for (job_id, _), v in zip(batch, vectors_of([t for _, t in batch]))])
            conn.commit()
            embedded += len(batch)
    except urllib.error.HTTPError as e:  # a model not pulled: 404 with the reason in the body
        print(f"WARNING embed: Ollama refused ({e.code}): {e.read().decode(errors='replace')[:200]}"
              + (f" - run `ollama pull {model}` on the laptop" if e.code == 404 else ""))
        return
    except urllib.error.URLError as e:
        print(f"WARNING embed: Ollama not reachable at {cfg['url']} ({e.reason}), skipped: {len(jobs)} jobs and {len(cvs)} CVs wait")
        return

    cv_vectors = [v for v, in conn.execute("SELECT vector FROM core.cv_vector WHERE model = %s", (model,))]
    if cv_vectors:  # no CV: cv_similarity stays empty
        vectors = conn.execute("SELECT job_id, vector FROM core.job_vector WHERE model = %s", (model,)).fetchall()
        with conn.cursor() as cur:
            cur.executemany("UPDATE core.job SET cv_similarity = %s WHERE job_id = %s",
                            [(max(cosine(v, cv) for cv in cv_vectors), job_id) for job_id, v in vectors])
        conn.commit()
    print(f"embed: {embedded} of {len(jobs)} jobs embedded, {len(cvs)} CVs embedded with {model}"
          f" in {time.monotonic() - started:.0f} s")
