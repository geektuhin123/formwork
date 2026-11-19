#!/usr/bin/env bash
# PostToolUse hook: ruff + the tests of the package that was just edited. Exit 2 surfaces failures to Claude.
set -uo pipefail
file=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))' 2>/dev/null || true)
root="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}"
rel="${file#"$root"/}"
case "$rel" in
  packages/*/*.py|apps/api/*.py|apps/worker/*.py) pkg=$(echo "$rel" | cut -d/ -f1-2) ;;
  *) exit 0 ;;
esac
cd "$root" || exit 0
out=$(uv run --quiet ruff check "$pkg" 2>&1) || { echo "$out" >&2; exit 2; }
if [ -d "$pkg/tests" ]; then
  out=$(uv run --quiet pytest "$pkg" -x -q 2>&1) || { echo "$out" | tail -40 >&2; exit 2; }
fi
exit 0
