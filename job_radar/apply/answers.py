"""The answers the apply runner may give a form: only Omar's approved entries, matched by wording. A question with no entry, a
select whose options do not offer the approved answer, or a number field with a non-number answer gets None, and the runner parks
the job (`park`) for Omar. Claude never writes an answer here: facts come from the file or not at all.

The file is `output/applications/apply-answers.yaml` (gitignored; see apply-answers.example.yaml): a list of
`{match: [phrases], answer: text}`."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from ..config import OUTPUT

ANSWERS_FILE = OUTPUT / "applications" / "apply-answers.yaml"


def load(path: Path | None = None) -> list[dict]:
    path = path or ANSWERS_FILE
    if not path.exists():
        return []
    return yaml.safe_load(path.read_text(encoding="utf-8")) or []


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split())


def resolve(question: str, field_type: str = "text", options: list[str] | None = None, entries: list[dict] | None = None) -> str | None:
    q = _norm(question)
    if not q:
        return None
    for entry in load() if entries is None else entries:
        if not any(_norm(phrase) in q for phrase in entry["match"]):
            continue
        answer = str(entry["answer"])
        if field_type == "number":
            return answer if re.fullmatch(r"\d+(\.\d+)?", answer.strip()) else None
        if options:
            return next((o for o in options if _norm(o) == _norm(answer)), None)
        return answer
    return None


def park(conn, job_id: int, question: str) -> None:
    conn.execute("INSERT INTO core.apply_question (job_id, question) VALUES (%s, %s) ON CONFLICT (job_id, question) DO NOTHING", (job_id, question))
