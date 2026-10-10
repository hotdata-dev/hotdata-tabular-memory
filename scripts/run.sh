#!/usr/bin/env bash
# Run one plugin script in the plugin's virtual environment.
# On a first run the environment is missing, so the install starts in the background
# and the call returns without touching the session.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA="${CLAUDE_PLUGIN_DATA:-$HOME/.claude/hotdata-memory}"
VENV="$DATA/venv"
NAME="$1"
shift

if [ ! -x "$VENV/bin/python" ] || [ ! -f "$VENV/.spec" ]; then
  mkdir -p "$DATA"
  if [ ! -f "$DATA/installing" ]; then
    : > "$DATA/installing"
    nohup bash "$ROOT/setup.sh" >"$DATA/setup.log" 2>&1 &
    printf '{"systemMessage":"hotdata-memory: installing hotmemory in the background; memory loads from the next session."}\n'
  fi
  exit 0
fi

PYTHONPATH="$ROOT" exec "$VENV/bin/python" "$ROOT/$NAME.py" "$@"
