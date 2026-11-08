"""What every form adapter shares: the one rule for taking an answer, and small text and file helpers."""

from __future__ import annotations

import re
from pathlib import Path


def norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split())


def clean_label(text: str) -> str:
    """A question as a person reads it: no required-marker or trailing colon, one line."""
    return re.sub(r"\s*[*✱:]+\s*$", "", " ".join((text or "").split())).strip()


def ask(resolve, question: str, field_type: str, options=None) -> str | None:
    """The only way an adapter gets an answer: `resolve` (Omar's approved answers). An empty answer, or one that is not exactly one of the
    form's own options when it has options, is no answer: the field stays empty and, if required, is handed back for Omar."""
    answer = resolve(question, field_type, list(options) if options else None)
    if answer is None:
        return None
    answer = str(answer).strip()
    if not answer or (options and answer not in options):
        return None
    return answer


def existing_file(path) -> str | None:
    """A PDF path the runner passed, only if the file is really there."""
    return str(path) if path and Path(str(path)).is_file() else None


def refuse_if_human(page) -> None:
    from .human_check import needs_human
    if needs_human(page):
        raise RuntimeError("this page needs a human (CAPTCHA, login wall or human check): the adapter must not fill or submit it")


def require_form(controls) -> None:
    """An empty list from `fill` means "ready to submit". A page with no form at all (closed posting, an embed this adapter does not know)
    must be an error, never that."""
    if not controls:
        raise RuntimeError("no application form on this page")
