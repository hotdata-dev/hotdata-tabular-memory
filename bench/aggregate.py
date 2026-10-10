"""Read every session result and print the per-arm comparison as JSON."""

from __future__ import annotations

import json
import os
import statistics
from collections import defaultdict
from pathlib import Path

import redundancy

HERE = Path(__file__).resolve().parent
TAG = os.environ.get("TAG", "")


def main() -> None:
    sources = [(HERE / "runs", ""), (HERE / "runs-v6", "_fixed"), (HERE / "runs-v21", "_v21"),
               (HERE / "runs-local", "")]
    arms: dict[str, list] = defaultdict(list)
    answers: dict[str, list] = defaultdict(list)
    agents = [(p, suffix, runs) for runs, suffix in sources if runs.is_dir()
              for p in sorted(runs.iterdir()) if p.is_dir()]
    runs_of: dict[str, Path] = {}
    for agent, suffix, runs in agents:
        arm = agent.name.rsplit("-r", 1)[0] + suffix
        runs_of[arm] = runs
        for path in sorted(agent.glob("s*.json")):
            row = json.loads(path.read_text())
            usage = row.get("usage") or {}
            arms[arm].append({
                "agent": agent.name + suffix,
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
                row = json.loads(line)
                row["agent"] = agent.name + suffix
                answers[arm].append(row)

    def stability(graded):
        """Count what happened to each question between pass 1 and pass 2.

        A question answered right in pass 1 and wrong in pass 2 is memory doing harm;
        the same wrong answer twice is an error that memory preserved.
        """
        seen = defaultdict(dict)
        for a in graded:
            seen[(a["agent"], a["id"])][a.get("pass", 1)] = a
        out = {"right_both": 0, "wrong_both_same": 0, "wrong_both_diff": 0,
               "right_to_wrong": 0, "wrong_to_right": 0, "pairs": 0}
        for v in seen.values():
            if len(v) != 2:
                continue
            out["pairs"] += 1
            one, two = v[1], v[2]
            if one["correct"] and two["correct"]:
                out["right_both"] += 1
            elif one["correct"]:
                out["right_to_wrong"] += 1
            elif two["correct"]:
                out["wrong_to_right"] += 1
            elif str(one["given"]) == str(two["given"]):
                out["wrong_both_same"] += 1
            else:
                out["wrong_both_diff"] += 1
        return out

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
            "stability": stability(graded),
            "swarm": redundancy.swarm_metrics(runs_of[arm], arm.split("_")[0]),
            "tools": redundancy.tool_use(runs_of[arm], arm.split("_")[0]),
            "invented_filter_errors": redundancy.invented_filter_errors(graded),
            "per_agent": sorted(
                [
                    {
                        "agent": name,
                        "accuracy": round(
                            sum(a["correct"] for a in graded if a["agent"] == name)
                            / max(sum(1 for a in graded if a["agent"] == name), 1), 4),
                        "cost": round(
                            sum(r["cost"] for r in rows if r["agent"] == name), 4),
                        "turns": sum(r["turns"] for r in rows if r["agent"] == name),
                    }
                    for name in sorted({r["agent"] for r in rows})
                ],
                key=lambda d: d["agent"],
            ),
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
        s = r.get("swarm") or {}
        print(f"{arm:9s} acc={r['accuracy']:.1%} cost=${r['cost_total']:.2f} "
              f"$/correct={r['cost_per_correct']:.3f} turns={r['turns_total']} "
              f"redundant={s.get('redundant_pct', '?')}% orient={s.get('orientation_queries', '?')} "
              f"invented={r['invented_filter_errors']}")
    print(f"\n-> {out}")


if __name__ == "__main__":
    main()
