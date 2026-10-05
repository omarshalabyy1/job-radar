#!/usr/bin/env python3
"""Validate active fenced Python examples and reject obvious secret patterns.

Historical `*.pre-2026-08-15.md` snapshots are immutable evidence of the previous
public state. They are intentionally excluded from active-example validation; the
current skill and references must satisfy the stricter checks.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_BLOCK = re.compile(r"```python\s*\n(.*?)```", re.DOTALL)
SECRET_PATTERNS = {
    "private key": re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
    "GitHub token": re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    "Telegram bot token": re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{20,}\b"),
    "credentialed URL": re.compile(r"https?://[^\s/:]+:[^\s/@]+@", re.I),
}
HISTORICAL_SUFFIX = ".pre-2026-08-15.md"


def is_historical(path: Path) -> bool:
    return path.name.endswith(HISTORICAL_SUFFIX)


def main() -> int:
    errors: list[str] = []
    checked = 0
    archived = 0
    for path in sorted(ROOT.rglob("*.md")):
        if ".git" in path.parts:
            continue
        if is_historical(path):
            archived += 1
            continue
        text = path.read_text(encoding="utf-8")
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                errors.append(f"{path.relative_to(ROOT)}: possible {label}")
        for index, match in enumerate(PYTHON_BLOCK.finditer(text), start=1):
            checked += 1
            try:
                ast.parse(match.group(1))
            except SyntaxError as exc:
                line = text[: match.start(1)].count("\n") + (exc.lineno or 1)
                errors.append(f"{path.relative_to(ROOT)}:{line}: Python block {index}: {exc.msg}")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"Validated {checked} active fenced Python blocks; preserved {archived} historical snapshots.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
