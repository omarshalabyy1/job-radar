#!/usr/bin/env bash
# The one "done" check for this repo. Prints GATE: PASS only if every step passes.
set -euo pipefail
cd "$(dirname "$0")"
PY=python; [ -x .venv/Scripts/python ] && PY=.venv/Scripts/python
echo "== tests"; $PY -m pytest tests -q
echo "== idempotency"; $PY scripts/idempotency_check.py
echo "GATE: PASS"
