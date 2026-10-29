"""The free models first (Omar, 2026-10-17): Groq, then Gemini, then a free OpenRouter model, each through its
OpenAI-compatible chat API and each under a daily cap below its free limit. A provider whose key is not in .env is
skipped. An answer that is not the JSON asked for, a 429 or any other error counts as a failure and the next
provider is asked. When none answers, the caller asks Claude (grade.py). Tailoring stays on Claude."""

from __future__ import annotations

import os
import re
import threading
from collections import Counter

import requests
from pydantic import BaseModel

# (name, key in .env, chat completions URL, model, calls a day: under the free tier's daily limit). Checked 2026-10-29
# with a real ~10k-token grade prompt: Gemini and OpenRouter answer; Groq's free tier refuses it (413, over its
# tokens a minute), so Groq is last, for the shorter prompts.
PROVIDERS = [
    ("gemini", "GOOGLE_GENERATIVE_AI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
     "gemini-3.1-flash-lite", 180),
    ("openrouter", "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1/chat/completions",
     "nvidia/nemotron-3-ultra-550b-a55b:free", 45),
    ("groq", "GROQ_API_KEY", "https://api.groq.com/openai/v1/chat/completions", "openai/gpt-oss-120b", 900),
]
LOCK = threading.Lock()
FAILED: Counter = Counter()  # failures per provider this run, for the step's summary line


def ask_json(system: str, user: str, answer: type[BaseModel], used: Counter, timeout: float = 30) -> tuple[str, BaseModel] | None:
    """(model, the parsed answer) from the first free provider that gives valid JSON, or None. used: calls made today
    per model (the caller seeds it from the grades already stored); a provider at its cap is skipped."""
    shape = f"\n\nAnswer with one JSON object only, matching this JSON schema:\n{answer.model_json_schema()}"
    for name, key, url, model, cap in PROVIDERS:
        if not os.environ.get(key):
            continue
        with LOCK:
            if used[model] >= cap:
                continue
            used[model] += 1
        try:
            r = requests.post(url, headers={"Authorization": f"Bearer {os.environ[key]}"}, timeout=timeout, json={
                "model": model, "temperature": 0, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system + shape}, {"role": "user", "content": user}]})
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
            return model, answer.model_validate_json(re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip()))
        except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as e:  # ValueError: bad JSON
            status = getattr(getattr(e, "response", None), "status_code", None)
            with LOCK:
                FAILED[f"{name} {f'HTTP {status}' if status else type(e).__name__}"] += 1
    return None
