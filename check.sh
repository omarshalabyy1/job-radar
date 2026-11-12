#!/usr/bin/env bash
# The one "done" check for this repo. Prints GATE: PASS only if every step passes.
set -euo pipefail
cd "$(dirname "$0")"
PY=python; [ -x .venv/Scripts/python ] && PY=.venv/Scripts/python; [ -x .venv/bin/python ] && PY=.venv/bin/python
# The content this run checks, as the tree a commit of it will have: the tracked files as they are now (a new file counts
# once staged). On a pass it is written to .git/gate-pass, which .githooks/pre-push needs before main is pushed.
index=$(mktemp); cp "$(git rev-parse --git-path index)" "$index"
tree=$(GIT_INDEX_FILE="$index" git add -u 2>/dev/null && GIT_INDEX_FILE="$index" git write-tree); rm -f "$index"
echo "== tests"; $PY -m pytest tests -q
echo "== idempotency"; $PY scripts/idempotency_check.py
echo "$tree" > "$(git rev-parse --git-path gate-pass)"
echo "GATE: PASS"
