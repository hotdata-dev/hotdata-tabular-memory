"""Write the text of each complaint from its spec, with Claude Haiku.

The category is fixed before the text exists (build_data.py), so it is the truth the
benchmark grades against. The writer is told the category and the trip, and told not
to name the category, so a reader has to understand the complaint to classify it.

Resumable: complaints already in data/complaints_raw.jsonl are skipped.

  python3 gen_complaints.py --limit 20     # a pilot batch
  python3 gen_complaints.py                # the rest
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPECS = HERE / "data" / "complaint_specs.jsonl"
RAW = HERE / "data" / "complaints_raw.jsonl"

MEANING = {
    "fare_dispute": "charged more than they should have been: the meter, a surcharge, "
                    "a flat airport fare not honoured, a card charged twice or wrongly",
    "detour": "the driver took a longer, slower or wrong route, or ignored the route "
              "the passenger asked for",
    "driver_conduct": "the driver was rude, dismissive, on the phone, refused a stop, "
                      "argued, or made inappropriate remarks, without physical danger",
    "vehicle_condition": "the cab itself was the problem: dirty, smelly, no heat, a "
                         "broken door, window, seat or card reader",
    "lost_item": "the passenger left something in the cab and wants it back",
    "safety": "the passenger was put in physical danger: speeding, running lights, "
              "phone use while weaving, a near collision, drowsy or impaired driving",
}

SYSTEM = "You write realistic passenger complaints for a city taxi commission dataset."

BRIEF = """Write one complaint for each trip below. Each complaint is about the issue
described for it, and only that issue is the reason for complaining.

Rules:
- 150 to 300 words, first person, as a real passenger would write to the commission.
- Vary the voice: some angry, some calm, some rambling, some terse and formal, some
  with typos. No two should open the same way.
- Use the trip facts loosely and consistently: the zones, roughly when, the fare if
  it matters. Never quote a trip id.
- About a third may mention a minor second annoyance in passing, but the main issue
  must be clear to a careful reader.
- Never use these words for the issue: fare dispute, detour, conduct, vehicle
  condition, lost item, safety.

Return only a JSON array: [{"complaint_id": <n>, "text": "<complaint>"}, ...]

Trips:
"""

lock = threading.Lock()


def describe(spec: dict) -> str:
    return json.dumps({
        "complaint_id": spec["complaint_id"],
        "issue": MEANING[spec["category"]],
        "pickup": spec["pickup_zone"], "dropoff": spec["dropoff_zone"],
        "when": spec["pickup"], "miles": spec["miles"], "fare": spec["fare"],
        "total_paid": spec["total"], "tip": spec["tip"], "passengers": spec["passengers"],
        "paid_by_card": spec["card"]})


def generate(batch: list[dict], model: str) -> tuple[list[dict], float]:
    prompt = BRIEF + "\n".join(describe(s) for s in batch)
    run = subprocess.run(
        ["claude", "-p", prompt, "--model", model, "--system-prompt", SYSTEM,
         "--setting-sources", "", "--strict-mcp-config", "--tools", "",
         "--disable-slash-commands", "--no-session-persistence", "--output-format", "json"],
        capture_output=True, text=True, timeout=600)
    result = json.loads(run.stdout)
    if result.get("is_error"):
        raise RuntimeError(result.get("result"))
    text = re.sub(r"^```(?:json)?\s*|\s*```\s*$", "", result["result"].strip())
    items = json.loads(text)
    wanted = {s["complaint_id"] for s in batch}
    return [i for i in items if i.get("complaint_id") in wanted and i.get("text")], \
        float(result.get("total_cost_usd") or 0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--batch", type=int, default=20)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--model", default="claude-haiku-5-5")
    args = parser.parse_args()

    done = set()
    if RAW.exists():
        done = {json.loads(line)["complaint_id"] for line in RAW.read_text().splitlines()}
    specs = [json.loads(line) for line in SPECS.read_text().splitlines()]
    todo = [s for s in specs if s["complaint_id"] not in done]
    if args.limit:
        todo = todo[:args.limit]
    batches = [todo[i:i + args.batch] for i in range(0, len(todo), args.batch)]
    print(f"{len(done)} done, {len(todo)} to write in {len(batches)} batches")

    spent = 0.0

    def work(batch: list[dict]) -> None:
        nonlocal spent
        try:
            items, cost = generate(batch, args.model)
        except Exception as error:                  # one bad batch is retried next run
            print(f"  batch from {batch[0]['complaint_id']} failed: {error}")
            return
        with lock, RAW.open("a") as out:
            for item in items:
                out.write(json.dumps({"complaint_id": item["complaint_id"],
                                      "text": item["text"].strip()}) + "\n")
            spent += cost
            print(f"  batch from {batch[0]['complaint_id']}: {len(items)}/{len(batch)}")

    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(work, batches))
    print(f"spent ${spent:.2f} (as the CLI prices it; Haiku 5.5 may be priced as Opus)")


if __name__ == "__main__":
    main()
