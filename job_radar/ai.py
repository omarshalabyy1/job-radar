"""Optional: Claude Haiku 4.5 scores a job against the CV; off without ANTHROPIC_API_KEY.

Anthropic SDK with structured outputs, so every answer parses. The CV is cv.pdf, cv.md or cv.txt
in the repo root (gitignored, never committed).
"""

from __future__ import annotations

import base64
from pathlib import Path

import anthropic
from pydantic import BaseModel

from .config import CANDIDATE

MODEL = "claude-haiku-4-5"
ROOT = Path(__file__).resolve().parent.parent

FIT_RULES = f"""You rate how well a job fits the candidate whose CV you are given, from 0 to 100.
90-100: apply today, the role, skills and seniority match. 70-89: a good match with small gaps.
50-69: a partial match. Below 50: a poor fit.
The candidate is {CANDIDATE}, looking for entry or mid-level roles, and prefers, in this order:
AI & data engineering, data engineering, AI engineering (NLP, LLM, agents, RAG), BI development
(Power BI, Excel), data analysis. Weigh the skills and years of experience the job asks for
against the CV, and whether its location or a visa or nationality requirement rules the candidate out.
The reason is one short sentence naming the main match or the main gap."""


class Fit(BaseModel):
    score: int
    reason: str


def cv_block() -> dict | None:
    """The CV as a content block, cached across the day's calls; None when there is no CV."""
    for name in ("cv.pdf", "cv.md", "cv.txt"):
        path = ROOT / name
        if not path.exists():
            continue
        if name.endswith(".pdf"):
            return {"type": "document", "cache_control": {"type": "ephemeral"},
                    "source": {"type": "base64", "media_type": "application/pdf",
                               "data": base64.standard_b64encode(path.read_bytes()).decode()}}
        return {"type": "text", "text": "My CV:\n\n" + path.read_text(encoding="utf-8"),
                "cache_control": {"type": "ephemeral"}}
    return None


def fit(client: anthropic.Anthropic, cv: dict, job: str) -> Fit | None:
    return client.messages.parse(
        model=MODEL, max_tokens=300, system=FIT_RULES,
        messages=[{"role": "user", "content": [cv, {"type": "text", "text": job}]}],
        output_format=Fit,
    ).parsed_output
