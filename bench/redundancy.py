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
# A shell double-quoted string: anything but a quote or backslash, or a backslash and
# whatever follows it. SQL in these commands carries \"PULocationID\" inside the quotes.
DQ = r'"((?:[^"\\]|\\.)*)"'
QUERY = re.compile(r"(?:hotdata query|hd query)\s+" + DQ, re.S)
BATCH_ARG = re.compile(DQ, re.S)
LABELLED = re.compile(r"^\s*(?:[A-Za-z_][A-Za-z0-9_]*\s*:\s*)?((?:select|with)\b.*)$", re.I | re.S)


def unescape(text: str) -> str:
    return text.replace('\\"', '"').replace("\\\\", "\\")
BATCH_HEREDOC = re.compile(r"hd batch\s*<<\s*'?(\w+)'?\n(.*?)\n\1", re.S)
LABEL_LINE = re.compile(r"^\s*--\s*\w+\s*$")


def norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip().lower()).rstrip(";")


def transcripts(runs: Path, arm: str) -> list[Path]:
    """Transcript files for every agent of `arm` under `runs`.

    Claude Code names a project's folder from its working directory, with every `/`
    turned into `-`, so the agent's work dir is the key.
    """
    found = []
    for agent in sorted(runs.glob(f"{arm}-r*")):
        # Only the sessions this run recorded. A rerun in the same work directory shares
        # the project folder with whatever ran there before, and those must not count.
        ids = set()
        for result in agent.glob("s*.json"):
            try:
                ids.add(json.loads(result.read_text()).get("session_id"))
            except (OSError, json.JSONDecodeError):
                pass
        name = str((agent / "work").resolve()).replace("/", "-")
        found.extend(path for path in (PROJECTS / name).glob("*.jsonl") if path.stem in ids)
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
                    out.append(norm(unescape(m.group(1))))
                if "hd batch" in cmd:
                    for m in BATCH_HEREDOC.finditer(cmd):
                        for stmt in m.group(2).split(";"):
                            body = "\n".join(l for l in stmt.splitlines() if not LABEL_LINE.match(l)).strip()
                            if body:
                                out.append(norm(body))
                    head = cmd.split("<<", 1)[0] if "<<" in cmd else cmd
                    head = head.split("hd batch", 1)[1]
                    for m in BATCH_ARG.finditer(head):
                        body = LABELLED.match(unescape(m.group(1)))
                        if body:
                            out.append(norm(body.group(1)))
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


def tool_use(runs: Path, arm: str) -> dict:
    """How the arm worked: tool calls by name, and the data files it opened."""
    import collections
    tools = collections.Counter()
    for path in transcripts(runs, arm):
        for line in open(path):
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("type") != "assistant":
                continue
            for c in d.get("message", {}).get("content", []):
                if c.get("type") == "tool_use":
                    tools[c["name"]] += 1
    return dict(tools)
