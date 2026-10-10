"""Keep only the complaints a second model classifies the way they were written.

A complaint a careful reader could fairly put in two categories would grade an agent
wrong for a defensible answer. So a different model reads each one blind, with the
same category definitions the agents get, and only agreements are kept.

Writes what the agents see and what grading uses, separately:
  data/complaints.parquet   complaint_id, trip_id, text
  data/truth.jsonl          complaint_id, trip_id, category, stratum

  python3 check_complaints.py               # classify what is not yet checked
  python3 check_complaints.py --finish      # write the two files from the checks
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import duckdb

from gen_complaints import MEANING

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
SPECS = DATA / "complaint_specs.jsonl"
RAW = DATA / "complaints_raw.jsonl"
CHECKS = DATA / "checks.jsonl"
OUT = DATA / "complaints.parquet"
TRUTH = DATA / "truth.jsonl"

SYSTEM = "You classify passenger complaints for a city taxi commission."

BRIEF = """Give each complaint the one category that is the passenger's main reason for
complaining. A minor annoyance mentioned in passing is not the main reason.

Categories:
""" + "\n".join(f"- {name}: {meaning}" for name, meaning in MEANING.items()) + """

Return only a JSON array: [{"complaint_id": <n>, "category": "<category>"}, ...]

Complaints:
"""

lock = threading.Lock()


def classify(batch: list[dict], model: str) -> list[dict]:
    prompt = BRIEF + "\n".join(json.dumps({"complaint_id": c["complaint_id"], "text": c["text"]})
                               for c in batch)
    run = subprocess.run(
        ["claude", "-p", prompt, "--model", model, "--system-prompt", SYSTEM,
         "--setting-sources", "", "--strict-mcp-config", "--tools", "",
         "--disable-slash-commands", "--no-session-persistence", "--output-format", "json"],
        capture_output=True, text=True, timeout=600)
    result = json.loads(run.stdout)
    if result.get("is_error"):
        raise RuntimeError(result.get("result"))
    text = re.sub(r"^```(?:json)?\s*|\s*```\s*$", "", result["result"].strip())
    return json.loads(text)


def check(args) -> None:
    done = set()
    if CHECKS.exists():
        done = {json.loads(line)["complaint_id"] for line in CHECKS.read_text().splitlines()}
    raw = [json.loads(line) for line in RAW.read_text().splitlines()]
    todo = [c for c in raw if c["complaint_id"] not in done]
    batches = [todo[i:i + args.batch] for i in range(0, len(todo), args.batch)]
    print(f"{len(done)} checked, {len(todo)} to check in {len(batches)} batches")

    def work(batch: list[dict]) -> None:
        try:
            labels = classify(batch, args.model)
        except Exception as error:
            print(f"  batch from {batch[0]['complaint_id']} failed: {error}")
            return
        wanted = {c["complaint_id"] for c in batch}
        with lock, CHECKS.open("a") as out:
            for item in labels:
                if item.get("complaint_id") in wanted:
                    out.write(json.dumps({"complaint_id": item["complaint_id"],
                                          "category": item.get("category")}) + "\n")
            print(f"  batch from {batch[0]['complaint_id']}: {len(labels)}/{len(batch)}")

    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(work, batches))


def finish() -> None:
    specs = {s["complaint_id"]: s for s in map(json.loads, SPECS.read_text().splitlines())}
    raw = {c["complaint_id"]: c for c in map(json.loads, RAW.read_text().splitlines())}
    checks = {c["complaint_id"]: c["category"] for c in map(json.loads, CHECKS.read_text().splitlines())}

    kept, confusion = [], Counter()
    for cid, text in sorted(raw.items()):
        if cid not in checks:
            continue
        written, read = specs[cid]["category"], checks[cid]
        confusion[(written, read)] += 1
        if written == read:
            kept.append(cid)

    print(f"{len(kept)} of {len(checks)} kept ({len(kept) / len(checks):.1%} agreement)")
    print("disagreements, written -> read:")
    for (w, r), n in confusion.most_common():
        if w != r:
            print(f"  {w:18} -> {r:18} {n}")

    with TRUTH.open("w") as out:
        for cid in kept:
            s = specs[cid]
            out.write(json.dumps({"complaint_id": cid, "trip_id": s["trip_id"],
                                  "category": s["category"], "stratum": s["stratum"]}) + "\n")
    rows = [(cid, specs[cid]["trip_id"], raw[cid]["text"]) for cid in kept]
    con = duckdb.connect()
    con.execute("CREATE TABLE c (complaint_id INTEGER, trip_id BIGINT, text VARCHAR)")
    con.executemany("INSERT INTO c VALUES (?, ?, ?)", rows)
    con.execute(f"COPY (SELECT * FROM c ORDER BY complaint_id) TO '{OUT}' (FORMAT parquet)")
    print(f"-> {OUT.name}, {TRUTH.name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--finish", action="store_true")
    parser.add_argument("--batch", type=int, default=25)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--model", default="claude-sonnet-5-5")
    args = parser.parse_args()
    finish() if args.finish else check(args)


if __name__ == "__main__":
    main()
