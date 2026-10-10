#!/usr/bin/env bash
# Build the plugin's virtual environment and install hotmemory from git.
# Safe to run again. Prints the interpreter it made.
set -euo pipefail

SPEC="${HOTDATA_MEMORY_SPEC:-hotmemory[hotdata,openai] @ git+https://github.com/hotdata-dev/hotmemory@main}"
DATA="${CLAUDE_PLUGIN_DATA:-$HOME/.claude/hotdata-memory}"
VENV="$DATA/venv"
PYTHON="${HOTDATA_MEMORY_PYTHON:-python3}"

mkdir -p "$DATA"
if [ ! -x "$VENV/bin/python" ]; then
  "$PYTHON" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --upgrade --quiet pip
"$VENV/bin/python" -m pip install --quiet --upgrade "$SPEC"
"$VENV/bin/python" - <<'PY'
import hotmemory, hotmemory.hotdata  # noqa: F401
print("hotmemory ready")
PY
printf '%s\n' "$SPEC" > "$VENV/.spec"
echo "$VENV/bin/python"
