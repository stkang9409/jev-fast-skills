#!/bin/bash
# Usage: fc-hint.sh "<intent>" <repo_root> [rel_dir...]
# Prints merged Quick Index Hint (index-only, no bundle body, default threshold 0.60) in a single parallel Jev pass.
set -euo pipefail

REAL_SCRIPT="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$REAL_SCRIPT")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
PY="$PLUGIN_ROOT/.venv/bin/python"
B="$SCRIPT_DIR/bundle_context.py"

INTENT="${1:?Usage: fc-hint.sh \"<intent>\" <repo_root> [rel_dir...]}"
ROOT="${2:-.}"
shift $(( $# >= 2 ? 2 : $# ))

T="${FC_THRESHOLD:-0.60}"

if [ "$#" -gt 0 ]; then
  IFS=','
  PATHS="$*"
  unset IFS
else
  PATHS="."
fi

exec "$PY" "$B" "$INTENT" --repo-root "$ROOT" --path "$PATHS" --file-threshold "$T" --symbol-threshold "$T" --hint
