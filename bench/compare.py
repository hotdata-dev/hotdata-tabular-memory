"""The four arms side by side: what each one was, and what it cost.

local      no Hotdata at all — the files on disk, whatever the machine has
bare       the Hotdata CLI from a brief, no plugin
plugin     the plugin, v2.0: one database, orientation, definitions
plugin_v21 the plugin, v2.1: the same plus hd batch and recent results
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARMS = [("local", "No Hotdata"), ("bare", "Hotdata CLI"),
        ("plugin", "Plugin v2.0"), ("plugin_v21", "Plugin v2.1")]


def pct(x: float) -> str:
    return f"{x:.1%}"


def main() -> None:
    r = json.loads((HERE / "report.json").read_text())
    arms = [(k, label, r[k]) for k, label in ARMS if k in r]
    rows = [
        ("sessions", lambda d: str(d["sessions"])),
        ("agents", lambda d: " ".join(f"{a['accuracy']:.0%}" for a in d["per_agent"])),
        ("correct", lambda d: pct(d["accuracy"])),
        ("  phase 3", lambda d: pct(d["accuracy_by_phase"].get("3", 0))),
        ("  phase 4", lambda d: pct(d["accuracy_by_phase"].get("4", 0))),
        ("right then wrong", lambda d: str(d["stability"]["right_to_wrong"])),
        ("turns / session", lambda d: f"{d['turns_total'] / d['sessions']:.1f}"),
        ("tool calls / session",
         lambda d: f"{sum(d.get('tools', {}).values()) / d['sessions']:.1f}"),
        ("tokens / session", lambda d: f"{sum(d['tokens'].values()) // d['sessions']:,}"),
        ("  of which output",
         lambda d: f"{d['tokens']['output'] // d['sessions']:,}"),
        ("tokens / correct",
         lambda d: f"{sum(d['tokens'].values()) // max(round(d['accuracy'] * d['questions']), 1):,}"),
        ("$ / session", lambda d: f"{d['cost_total'] / d['sessions']:.3f}"),
        ("$ / correct", lambda d: f"{d['cost_per_correct']:.3f}"),
        ("seconds / session", lambda d: f"{d['wall_mean_s']:.0f}"),
    ]
    width = max(len(name) for name, _ in rows) + 2
    print(" " * width + "".join(f"{label:>14s}" for _, label, _ in arms))
    for name, get in rows:
        print(f"{name:{width}s}" + "".join(f"{get(d):>14s}" for _, _, d in arms))

    print("\ntools used")
    names = sorted({n for _, _, d in arms for n in d.get("tools", {})})
    for n in names:
        print(f"  {n:{width - 2}s}" + "".join(f"{d.get('tools', {}).get(n, 0):>14d}"
                                              for _, _, d in arms))

    print("\nqueries against Hotdata (none by design for the first arm)")
    for key, label, d in arms:
        s = d.get("swarm") or {}
        print(f"  {label:{width - 2}s}{s.get('queries', 0):>14d}"
              f"   redundant {s.get('redundant_pct', 0):.0f}%"
              f"   orientation {s.get('orientation_queries', 0)}")


if __name__ == "__main__":
    main()
