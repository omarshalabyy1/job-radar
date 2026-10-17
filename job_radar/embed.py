"""embed: local Ollama vectors for the jobs and your CVs: how close each job is to your CV.

Each job still in the window (config.WORK_DAYS), in any place, with a description (a title alone is a
weak signal) and no vector yet from settings.yaml embeddings.model gets one from its title, company and the
start of its description, into core.job_vector. Your CVs in core.cv are embedded when they have no vector from that model
yet or were uploaded after their vector was made (core.cv_vector.embedded_at; a CV uploaded again keeps its label), into
core.cv_vector. Each sentence of your CVs' experience and projects (cv_lines) is embedded once, into core.cv_line. Then
core.job.cv_similarity = 0.55 x how close the job is to your closest CV + 0.45 x how close it is to your closest single CV
line (cosine, 0-1), so one strong project counts even inside an unrelated CV, and core.job.cv_evidence = that line.
The job_radar DAG runs it after match_skills. With features.embeddings false,
or Ollama not running or without the model, it prints why and stops green (a WARNING line when jobs or CVs were waiting)."""

from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import numpy as np

from .config import FEATURES, WORK_DAYS, SETTINGS
from .sources import time_left

EMBED_SECONDS = 200  # the DAG gives the task 240 s; the jobs left are embedded next run
BATCH = 32  # texts per Ollama call
WHOLE, LINE = 0.55, 0.45  # the weight of the closest whole CV and of the closest single CV line in cv_similarity
# a CV's experience and projects: from the EXPERIENCE heading to EDUCATION; an entry's header ("Title — Company |
# Place Apr 2026 – Present (6 mos)" or "Project — tagline (GitHub) 2026") ends at its last year or length
EXPERIENCE, EDUCATION = re.compile(r"\bEXPERIENCE\b"), re.compile(r"\bEDUCATION\b")
HEADER_END = re.compile(r"(?:\b(?:19|20)\d{2}|\(\d+ (?:mos?|yrs?)\))\s+(?=[A-Z])")


def cv_lines(text: str) -> list[str]:
    """The sentences of a CV's experience and projects, headers and "Technologies:" lists left out, each once."""
    start = EXPERIENCE.search(text)
    end = EDUCATION.search(text, start.end() if start else 0)
    lines = []
    for piece in re.split(r"(?<=\.)\s+", text[start.end() if start else 0:end.start() if end else len(text)]):
        if " — " in piece:  # an entry's header, then its first sentence
            cuts = list(HEADER_END.finditer(piece))
            if not cuts:
                continue
            piece = piece[cuts[-1].end():]
        piece = piece.strip()
        if not piece.startswith("Technologies:") and 40 <= len(piece) <= 320:
            lines.append(piece)
    return list(dict.fromkeys(lines))


def match(job_vectors: list, cv_vectors: list, line_vectors: list, lines: list) -> list[tuple[float, str | None]]:
    """(cv_similarity, cv_evidence) of each job: WHOLE x its closest CV + LINE x its closest CV line, and that line;
    the closest CV alone, and no line, when there are no lines."""
    def unit(vectors):
        m = np.asarray(vectors, dtype=float)
        norms = np.linalg.norm(m, axis=1, keepdims=True)
        return np.divide(m, norms, out=np.zeros_like(m), where=norms > 0)

    jobs = unit(job_vectors)
    whole = (jobs @ unit(cv_vectors).T).max(axis=1)
    if not lines:
        return [(float(w), None) for w in whole]
    by_line = jobs @ unit(line_vectors).T
    best = by_line.argmax(axis=1)
    return [(float(WHOLE * w + LINE * by_line[i, b]), lines[b]) for i, (w, b) in enumerate(zip(whole, best))]


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
        " WHERE j.in_scope AND j.first_seen >= current_date - %s AND j.description IS NOT NULL"
        " AND NOT EXISTS (SELECT 1 FROM core.job_vector v WHERE v.job_id = j.job_id AND v.model = %s)"
        " ORDER BY j.job_id", (WORK_DAYS, model)).fetchall()
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
        lines = list(dict.fromkeys(line for (text,) in conn.execute("SELECT text FROM core.cv") for line in cv_lines(text)))
        conn.execute("DELETE FROM core.cv_line WHERE model <> %s OR NOT line = ANY(%s)", (model, lines))  # a line no CV has now
        have = {line for (line,) in conn.execute("SELECT line FROM core.cv_line")}
        todo = [line for line in lines if line not in have]
        for i in range(0, len(todo), BATCH):
            with conn.cursor() as cur:
                cur.executemany("INSERT INTO core.cv_line (line, vector, model) VALUES (%s, %s, %s)",
                                [(line, v, model) for line, v in zip(todo[i:i + BATCH], vectors_of(todo[i:i + BATCH]))])
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
    vectors = conn.execute("SELECT job_id, vector FROM core.job_vector WHERE model = %s", (model,)).fetchall()
    if cv_vectors and vectors:  # no CV: cv_similarity stays empty
        lines = conn.execute("SELECT line, vector FROM core.cv_line WHERE model = %s ORDER BY line", (model,)).fetchall()
        scores = match([v for _, v in vectors], cv_vectors, [v for _, v in lines], [line for line, _ in lines])
        with conn.cursor() as cur:
            cur.executemany("UPDATE core.job SET cv_similarity = %s, cv_evidence = %s WHERE job_id = %s",
                            [(similarity, line, job_id) for (job_id, _), (similarity, line) in zip(vectors, scores)])
        conn.commit()
    print(f"embed: {embedded} of {len(jobs)} jobs embedded, {len(cvs)} CVs embedded with {model}"
          f" in {time.monotonic() - started:.0f} s")
