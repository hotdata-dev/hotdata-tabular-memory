"""Swarm metrics read from the transcripts a run leaves behind.

For each arm: the share of data queries some agent had already run, how many were
orientation (`LIMIT 1/2`, information_schema), and how many answers carry the
invented-filter signature (wrong, but within 5% of the truth on the route questions).
"""

from __future__ import annotations

import glob
import json
import re
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECTS = Path.home() / ".claude" / "projects"
SCHEMA = re.compile(r"information_schema|\bdescribe\b|\bshow\s+(tables|columns)", re.I)
PEEK = re.compile(r"\bselect\s+\*|\blimit\s+[1-5]\b", re.I)
RANKING = re.compile(r"\border\s+by\b|\bgroup\s+by\b|\b(count|sum|avg|min|max|median)\s*\(", re.I)


def is_orientation(sql: str) -> bool:
    """A look at the shape of the data, as opposed to a question about it.

    Schema reads always count. A `SELECT *` or a tiny LIMIT counts only when the query
    is not ranking or aggregating — `ORDER BY fare DESC LIMIT 1` is an answer, not a peek.
    """
    if SCHEMA.search(sql):
        return True
    return bool(PEEK.search(sql)) and not RANKING.search(sql)
QUERY = re.compile(r'(?:hotdata query|hd query)\s+"(.+?)"\s*(?:-d|\||$|;)', re.S)


def norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip().lower()).rstrip(";")


def transcripts(runs: Path, arm: str) -> list[Path]:
    """Transcript files for every agent of `arm` under `runs`.

    Claude Code names a project's folder from its working directory, with every `/`
    turned into `-`, so the agent's work dir is the key.
    """
    found = []
    for agent in sorted(runs.glob(f"{arm}-r*")):
        name = str((agent / "work").resolve()).replace("/", "-")
        found.extend((PROJECTS / name).glob("*.jsonl"))
    return found


def queries_of(path: Path) -> list[str]:
    out = []
    for line in open(path):
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("type") != "assistant":
            continue
        for c in d.get("message", {}).get("content", []):
            if c.get("type") == "tool_use" and c.get("name") == "Bash":
                cmd = (c.get("input") or {}).get("command", "")
                for m in QUERY.finditer(cmd):
                    out.append(norm(m.group(1)))
    return out


def swarm_metrics(runs: Path, arm: str) -> dict:
    all_q, per_agent = [], defaultdict(list)
    for path in transcripts(runs, arm):
        agent = path.parent.name
        qs = queries_of(path)
        per_agent[agent].extend(qs)
        all_q.extend(qs)
    distinct = len(set(all_q))
    orient = sum(1 for q in all_q if is_orientation(q))
    return {
        "queries": len(all_q),
        "distinct": distinct,
        "redundant_pct": round(100 * (1 - distinct / len(all_q)), 1) if all_q else 0.0,
        "orientation_queries": orient,
        "agents": len(per_agent),
    }


def invented_filter_errors(answers: list[dict]) -> int:
    """Wrong answers on the per-route questions that sit within 5% of the truth."""
    count = 0
    for a in answers:
        if a["correct"] or a["phase"] != 2:
            continue
        try:
            given, truth = float(str(a["given"]).replace(",", "")), float(a["answer"])
        except (TypeError, ValueError):
            continue
        if truth and abs(given - truth) / abs(truth) < 0.05:
            count += 1
    return count
