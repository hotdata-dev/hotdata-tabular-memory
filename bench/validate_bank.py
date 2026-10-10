"""Run every question's ground-truth SQL against Hotdata and compare with DuckDB.

The agents query Hotdata, so a truth that only DuckDB agrees with would mark a correct
answer wrong. Any mismatch here is a bug in the bank, not in the agent.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = "dbid_redacted_benchmark"
PREFIX = ("WITH t AS (SELECT * FROM taxi.public.trips), "
          "z AS (SELECT * FROM taxi.public.zones) ")


def ask(sql: str) -> str:
    out = subprocess.run(
        ["hotdata", "query", PREFIX + sql, "-d", DB, "-o", "csv"],
        capture_output=True, text=True, timeout=600,
    )
    rows = [r for r in out.stdout.strip().splitlines() if r]
    if len(rows) < 2:
        return f"ERROR {out.stderr.strip()[:120] or out.stdout.strip()[:120]}"
    return rows[1].strip().strip('"')


def main() -> None:
    questions = json.loads((HERE / "questions.json").read_text())
    bad = []
    for q in questions:
        if "DESCRIBE" in q["sql"]:
            print(f"  {q['id']} skipped (DESCRIBE)")
            continue
        got = ask(q["sql"])
        if q["kind"] == "number":
            try:
                ok = abs(float(got) - q["answer"]) <= max(q["tol"], 0.005)
            except ValueError:
                ok = False
        else:
            ok = got == q["answer"]
        print(f"  {q['id']} {'ok ' if ok else 'MISMATCH'} duckdb={q['answer']} hotdata={got}")
        if not ok:
            bad.append(q["id"])
    print(f"\n{len(questions) - len(bad) - 1}/{len(questions) - 1} agree"
          + (f"; mismatched: {', '.join(bad)}" if bad else ""))


if __name__ == "__main__":
    main()
