#!/usr/bin/env bash
# Build the development environment outside the plugin directory and run the tests.
# The plugin directory is copied whole when the plugin installs, so nothing heavy
# belongs in it.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${HOTDATA_MEMORY_DEV_VENV:-$HOME/.cache/hotdata-memory/dev-venv}"
SPEC="${HOTDATA_MEMORY_SPEC:-hotmemory[hotdata,openai] @ git+https://github.com/hotdata-dev/hotmemory@main}"

if [ ! -x "$VENV/bin/python" ]; then
  mkdir -p "$(dirname "$VENV")"
  python3 -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet --upgrade "$SPEC" pytest

exec "$VENV/bin/python" -m pytest "$ROOT/tests" "$@"
