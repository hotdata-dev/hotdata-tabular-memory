#!/usr/bin/env bash
# Run the benchmark: four replicates of each arm, the three arms side by side.
#
# The arms run together in each round so a slow period on the API hits all three
# equally. Sessions inside one agent stay sequential, because memory has to cross a
# session boundary to be worth anything.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${1:-$HERE/runs}"
REPLICATES="${REPLICATES:-4}"
SESSIONS="${SESSIONS:-10}"

mkdir -p "$OUT"
for r in $(seq 1 "$REPLICATES"); do
  echo "=== replicate $r of $REPLICATES ==="
  pids=()
  for arm in markdown hotdata none; do
    python3 "$HERE/runner.py" --arm "$arm" --replicate "$r" --out "$OUT" \
      --sessions "$SESSIONS" > "$OUT/$arm-r$r.log" 2>&1 &
    pids+=($!)
  done
  for pid in "${pids[@]}"; do wait "$pid"; done
  for arm in markdown hotdata none; do
    tail -1 "$OUT/$arm-r$r.log"
  done
done
echo "=== all agents done ==="
