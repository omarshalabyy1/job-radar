#!/usr/bin/env python3
"""Fail when PyPI's latest Scrapling release differs from UPSTREAM_VERSION."""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINNED = (ROOT / "UPSTREAM_VERSION").read_text(encoding="utf-8").strip()
PYPI = "https://pypi.org/pypi/scrapling/json"


def main() -> int:
    request = urllib.request.Request(PYPI, headers={"User-Agent": "scrapling-skill-upstream-check/1"})
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.load(response)
    latest = payload["info"]["version"]
    print(f"pinned={PINNED} latest={latest}")
    if latest != PINNED:
        print(
            "Upstream drift detected. Re-verify imports, signatures, examples, security defaults and release notes before updating UPSTREAM_VERSION.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
