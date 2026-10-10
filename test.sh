#!/usr/bin/env bash
# Run the tests. The plugin itself needs only the standard library and the
# `hotdata` CLI; pytest lives in a cache outside the plugin directory, because
# the whole directory is copied when the plugin installs.
set -euo pipefail
VENV="${HOTDATA_MEMORY_TEST_VENV:-$HOME/.cache/hotdata-memory/test-venv}"
if [ ! -x "$VENV/bin/python" ]; then
  mkdir -p "$(dirname "$VENV")"
  python3 -m venv "$VENV"
  "$VENV/bin/python" -m pip install --quiet --upgrade pip pytest
fi
exec "$VENV/bin/python" -m pytest "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/tests" "$@"
