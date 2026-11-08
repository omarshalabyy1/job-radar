"""The contract between the apply runner and a board's form adapter (docs/superpowers/plans/2026-11-08-auto-apply.md, Task 4).
An adapter drives one board's application form with a Playwright `page`; it never decides which job to apply to, never invents an
answer (it asks `resolve`, which returns None when Omar has not approved one), never logs in and never solves a CAPTCHA."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

# resolve(question, field_type, options) -> approved answer or None (job_radar.apply.answers.resolve)
Resolve = Callable[[str, str, "list[str] | None"], "str | None"]


@dataclass(frozen=True)
class Field:
    label: str
    kind: str  # "text" | "number" | "select" | "file" | "checkbox"
    options: tuple[str, ...] = ()
    required: bool = True


@dataclass(frozen=True)
class Receipt:
    confirmed: bool  # the page showed the board's own confirmation
    text: str = ""   # that confirmation text, stored as proof


class Adapter(Protocol):
    name: str  # the board name queue.board_of returns ("greenhouse", "lever", ...)

    def can_handle(self, url: str) -> bool: ...

    def needs_human(self, page) -> bool:
        """A CAPTCHA, login wall or checkpoint is on the page: the runner parks the job and sends ntfy; nothing is submitted."""

    def fill(self, page, job, resolve: Resolve) -> list[Field]:
        """Fill every field it can from approved answers and the job's tailored CV and cover letter PDFs; return the required
        fields it could NOT answer (empty list = ready to submit). Never click submit here."""

    def submit(self, page) -> Receipt:
        """Click submit once and read the confirmation. confirmed=False when no confirmation shows."""
