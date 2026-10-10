"""Read every session result and print the per-arm comparison as JSON."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> None:
    runs = HERE / "runs"
    arms: dict[str, list] = defaultdict(list)
    answers: dict[str, list] = defaultdict(list)
    for agent in sorted(p for p in runs.iterdir() if p.is_dir()):
        arm = agent.name.rsplit("-r", 1)[0]
        for path in sorted(agent.glob("s*.json")):
            row = json.loads(path.read_text())
            usage = row.get("usage") or {}
            arms[arm].append({
                "agent": agent.name,
                "session": row.get("session_index"),
                "pass": row.get("pass", 1),
                "cost": row.get("total_cost_usd", 0.0),
                "turns": row.get("num_turns", 0),
                "wall": row.get("wall_seconds", 0.0),
                "api_ms": row.get("duration_api_ms", 0),
                "input": usage.get("input_tokens", 0),
                "output": usage.get("output_tokens", 0),
                "cache_read": usage.get("cache_read_input_tokens", 0),
                "cache_write": usage.get("cache_creation_input_tokens", 0),
                "error": bool(row.get("is_error")),
            })
        path = agent / "answers.jsonl"
        if path.exists():
            for line in path.read_text().splitlines():
                answers[arm].append(json.loads(line))

    report = {}
    for arm, rows in arms.items():
        graded = answers[arm]
        by_phase = defaultdict(list)
        by_session = defaultdict(list)
        by_pass = defaultdict(list)
        for a in graded:
            by_phase[a["phase"]].append(a["correct"])
            by_session[a["session"]].append(a["correct"])
            by_pass[a.get("pass", 1)].append(a["correct"])
        pass_cost = defaultdict(list)
        pass_turns = defaultdict(list)
        for r in rows:
            pass_cost[r["pass"]].append(r["cost"])
            pass_turns[r["pass"]].append(r["turns"])
        costs = [r["cost"] for r in rows]
        report[arm] = {
            "agents": len({r["agent"] for r in rows}),
            "sessions": len(rows),
            "questions": len(graded),
            "accuracy": round(sum(a["correct"] for a in graded) / max(len(graded), 1), 4),
            "accuracy_by_phase": {
                str(p): round(sum(v) / len(v), 4) for p, v in sorted(by_phase.items())
            },
            "accuracy_by_session": {
                str(s): round(sum(v) / len(v), 4) for s, v in sorted(by_session.items())
            },
            "accuracy_by_pass": {
                str(k): round(sum(v) / len(v), 4) for k, v in sorted(by_pass.items())
            },
            "cost_by_pass": {
                str(k): round(sum(v), 4) for k, v in sorted(pass_cost.items())
            },
            "turns_by_pass": {
                str(k): sum(v) for k, v in sorted(pass_turns.items())
            },
            "cost_total": round(sum(costs), 4),
            "cost_per_session_mean": round(statistics.mean(costs), 4) if costs else 0,
            "cost_per_correct": round(
                sum(costs) / max(sum(a["correct"] for a in graded), 1), 4),
            "tokens": {
                k: sum(r[k] for r in rows)
                for k in ("input", "output", "cache_read", "cache_write")
            },
            "turns_total": sum(r["turns"] for r in rows),
            "wall_mean_s": round(statistics.mean([r["wall"] for r in rows]), 1) if rows else 0,
            "errors": sum(r["error"] for r in rows),
            "sessions_detail": rows,
        }
    out = HERE / "report.json"
    out.write_text(json.dumps(report, indent=1) + "\n")
    for arm, r in sorted(report.items()):
        print(f"{arm:9s} acc={r['accuracy']:.1%} cost=${r['cost_total']:.2f} "
              f"$/correct={r['cost_per_correct']:.3f} turns={r['turns_total']} "
              f"errors={r['errors']}")
    print(f"\n-> {out}")


if __name__ == "__main__":
    main()
