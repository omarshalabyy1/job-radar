#!/usr/bin/env bash
# The one "done" check for this repo. Prints GATE: PASS only if every step passes.
set -euo pipefail
cd "$(dirname "$0")"
echo "== tests"; python -m pytest tests -q
echo "GATE: PASS"
