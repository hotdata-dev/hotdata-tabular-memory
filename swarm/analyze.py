"""Summarise swarm runs: cost, accuracy, and what happened to the classification work.

For every run directory under runs*/ it reports:

  cost       dollars, tokens, turns and seconds per task, and the cost of the first
             and last third of tasks — falling cost is the shared store paying off
  accuracy   answers within tolerance, and recorded labels that match the truth
  reasoning  label rows recorded per distinct complaint: 1.0 means nothing was
             classified twice
  agreement  complaints two agents labelled differently
  integrity  label rows that could not be read, or name no known category

  python3 analyze.py            # every run
  python3 analyze.py runs-pilot # one directory of runs
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from build_data import CATEGORIES

HERE = Path(__file__).resolve().parent
BENCH = Path.home() / "swarm-bench"
PROJECTS = Path.home() / ".claude" / "projects"
# Files only the harness may read, and the shared scratch space agents must not use.
SPILL = re.compile(r"/private/tmp/claude-\d+/[^\s'\"]*")
FORBIDDEN = ("truth.jsonl", "complaint_specs", "checks.jsonl", "complaints_raw", "/tmp/")


def leaks(session: dict) -> list[str]:
    """Forbidden paths this session's tool calls mention."""
    sid = session.get("session_id")
    found = set()
    for path in PROJECTS.glob(f"*/{sid}.jsonl") if sid else []:
        for line in path.read_text().splitlines():
            e = json.loads(line)
            if e.get("type") != "assistant":
                continue
            for c in e["message"].get("content", []):
                if c.get("type") == "tool_use":
                    # Claude Code saves a large tool result under /private/tmp/claude-<uid>/
                    # in a directory of the session's own; reading it back is not a leak.
                    text = SPILL.sub("", json.dumps(c["input"]))
                    found.update(f for f in FORBIDDEN if f in text)
    return sorted(found)


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


def summarise(run: Path, truth: dict, tasks: dict) -> dict:
    meta = json.loads((run / "run.json").read_text())
    sessions = sorted((json.loads(p.read_text()) for p in (run / "sessions").glob("t*.json")),
                      key=lambda s: s["task"])
    order = {t: i for i, t in enumerate(meta["tasks"])}
    sessions.sort(key=lambda s: order[s["task"]])

    def tokens(s: dict) -> int:
        u = s.get("usage") or {}
        return sum(u.get(k, 0) for k in ("input_tokens", "output_tokens",
                                          "cache_read_input_tokens", "cache_creation_input_tokens"))

    # A session that failed before working (a spend limit, a crash) is not a cheap
    # task. It is left out of every average and counted under `errors`.
    failed = [s for s in sessions if s.get("is_error")]
    sessions = [s for s in sessions if not s.get("is_error")]
    costs = [s.get("total_cost_usd") or 0 for s in sessions]
    third = max(1, len(sessions) // 3)
    labels = load_jsonl(run / "labels.jsonl")
    by_complaint: dict[int, set] = defaultdict(set)
    for l in labels:
        by_complaint[l["complaint_id"]].add(l["category"])
    known = [l for l in labels if l["category"] in CATEGORIES]
    scored = [l for l in known if l["complaint_id"] in truth]
    needed = {c for t in meta["tasks"] for c in tasks[t]["needs"]}
    models = sorted({m for s in sessions for m in (s.get("modelUsage") or {})
                     if "haiku" not in m or "haiku" in meta["model"]})

    return {
        "run": run.name, "arm": meta["arm"], "concurrency": meta["concurrency"],
        "tasks": len(sessions), "models": models,
        "cli": sorted({s.get("cli_version", "") for s in sessions}),
        "correct": sum(s.get("correct", False) for s in sessions) / max(1, len(sessions)),
        "cost_task": statistics.mean(costs) if costs else 0,
        "cost_first_third": statistics.mean(costs[:third]) if costs else 0,
        "cost_last_third": statistics.mean(costs[-third:]) if costs else 0,
        "tokens_task": statistics.mean(map(tokens, sessions)) if sessions else 0,
        "turns_task": statistics.mean(s.get("num_turns") or 0 for s in sessions) if sessions else 0,
        "secs_task": statistics.mean(s["end"] - s["start"] for s in sessions) if sessions else 0,
        "wall_run": max((s["end"] for s in sessions), default=0),
        "label_rows": len(labels),
        "distinct_labelled": len(by_complaint),
        "rows_per_complaint": len(labels) / max(1, len(by_complaint)),
        "needed_distinct": len(needed),
        "label_accuracy": sum(truth[l["complaint_id"]] == l["category"] for l in scored)
                          / max(1, len(scored)),
        "disagreements": sum(len(c) > 1 for c in by_complaint.values()),
        "unknown_category": len(labels) - len(known),
        "unreadable": len(json.loads((run / "unreadable.json").read_text()))
                      if (run / "unreadable.json").exists() else 0,
        "errors": len(failed),
        "leaks": {s["task"]: l for s in sessions if (l := leaks(s))},
    }


def main() -> None:
    roots = [BENCH / a for a in sys.argv[1:]] or sorted(BENCH.glob("runs*"))
    truth = {t["complaint_id"]: t["category"] for t in load_jsonl(HERE / "data" / "truth.jsonl")}
    tasks = {t["id"]: t for t in json.loads((HERE / "tasks.json").read_text())}
    rows = [summarise(run, truth, tasks)
            for root in roots for run in sorted(root.iterdir())
            if (run / "run.json").exists() and (run / "sessions").exists()]
    (HERE / "report.json").write_text(json.dumps(rows, indent=1) + "\n")

    cols = [("run", "{:<22}"), ("correct", "{:>7.0%}"), ("cost_task", "${:>5.2f}"),
            ("cost_first_third", "${:>5.2f}"), ("cost_last_third", "${:>5.2f}"),
            ("turns_task", "{:>5.1f}"), ("secs_task", "{:>5.0f}"),
            ("rows_per_complaint", "{:>5.2f}"), ("label_accuracy", "{:>6.1%}"),
            ("disagreements", "{:>5}"), ("unreadable", "{:>5}"), ("errors", "{:>4}")]
    print("  ".join(c for c, _ in cols))
    for r in rows:
        print("  ".join(f.format(r[c]) for c, f in cols))
        flags = []
        if len(r["cli"]) > 1:
            flags.append("MIXED CLI")
        if len(r["models"]) != 1:
            flags.append(f"MODELS {r['models']}")
        if r["leaks"]:
            flags.append(f"LEAKS {r['leaks']}")
        if flags:
            print("    " + "; ".join(flags))


if __name__ == "__main__":
    main()
