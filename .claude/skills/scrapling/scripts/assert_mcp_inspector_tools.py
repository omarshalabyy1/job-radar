#!/usr/bin/env python3
"""Assert that MCP Inspector returned the documented Scrapling MCP tool surface."""

from __future__ import annotations

import json
import sys
from pathlib import Path

EXPECTED = {
    "make_request",
    "open_request_session",
    "session_fetch",
}


def tool_names(value: object) -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        tools = value.get("tools")
        if isinstance(tools, list):
            for item in tools:
                if isinstance(item, dict) and isinstance(item.get("name"), str):
                    names.add(item["name"])
        for nested in value.values():
            names.update(tool_names(nested))
    elif isinstance(value, list):
        for nested in value:
            names.update(tool_names(nested))
    return names


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: assert_mcp_inspector_tools.py <inspector-json>")

    path = Path(sys.argv[1])
    raw = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        preview = raw[:1000].replace("\n", "\\n")
        raise AssertionError(
            f"MCP Inspector did not emit valid JSON: {exc}; output prefix={preview!r}"
        ) from exc

    names = tool_names(payload)
    missing = EXPECTED - names
    if missing:
        raise AssertionError(
            f"documented Scrapling MCP tools missing from Inspector output: "
            f"{sorted(missing)}; observed={sorted(names)}"
        )

    print(
        "MCP Inspector black-box tools/list OK; "
        f"required={sorted(EXPECTED)} observed_count={len(names)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
