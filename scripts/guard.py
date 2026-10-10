"""PreToolUse hook: a large data file goes to Hotdata, not into context.

An agent that can read 50MB into its context will. A refusal that names the exact
command to run instead holds where an instruction would not — that was the one
behavioural finding of the benchmark that never varied.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import project

LOADERS = re.compile(r"read_csv\(|read_parquet\(|read_json\(|duckdb|pl\.read_|pl\.scan_|"
                     r"\bpd\.read_", re.I)
PATHS = re.compile(r"""['"]([^'"]+\.(?:csv|tsv|parquet|jsonl?|ndjson))['"]""", re.I)


def threshold_bytes() -> int:
    try:
        return int(float(project.option("guard_threshold_mb", "8")) * 1024 * 1024)
    except ValueError:
        return 8 * 1024 * 1024


def is_large_data_file(path: Path) -> int:
    """Return the size if `path` is a data file over the threshold, else 0."""
    if path.suffix.lower() not in project.DATA_EXTENSIONS:
        return 0
    try:
        size = path.stat().st_size
    except OSError:
        return 0
    return size if size > threshold_bytes() else 0


def reason(path: Path, size: int) -> str:
    mb = size / (1024 * 1024)
    if path.suffix.lower() == ".parquet":
        cost = "a binary file: read as text it is noise, and the first 2,000 lines of noise"
    else:
        cost = (f"on the order of {int(size / 3.6 / 1000):,}k tokens, and the first 2,000 "
                f"lines")
    return (f"{path.name} is {mb:.0f}MB. Read into context that is {cost} tell you nothing a "
            f"query would not. Load it into Hotdata and query it there:\n\n"
            f"  hd load {path}\n\n"
            f"That takes a few seconds, prints the schema and a sample, and after it every "
            f"question is one `hd query \"<sql>\"` that returns an answer, not the table. "
            f"Set the plugin option read_guard=off to read the file anyway.")


def deny(text: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": text}}))


def nudge(text: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "additionalContext": text}}))


def main() -> int:
    if project.option("read_guard", "on").lower() in ("off", "false", "0", "no"):
        return 0
    payload = json.loads(sys.stdin.read() or "{}")
    tool = payload.get("tool_name", "")
    inputs = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or os.getcwd()
    try:
        if tool == "Read":
            raw = inputs.get("file_path", "")
            if raw:
                path = Path(raw).expanduser()
                if not path.is_absolute():
                    path = Path(cwd) / path
                size = is_large_data_file(path)
                if size:
                    deny(reason(path, size))
        elif tool == "Bash":
            command = inputs.get("command", "")
            if LOADERS.search(command):
                named = [Path(cwd) / p if not Path(p).is_absolute() else Path(p)
                         for p in PATHS.findall(command)]
                large = next(((p, is_large_data_file(p)) for p in named
                              if is_large_data_file(p)), None)
                if large:
                    deny(reason(*large))
                elif not named:
                    nudge("This looks like a local data load. For a file over a few MB, "
                          "`hd load <file>` puts it in Hotdata in seconds and every question "
                          "after that is a query that returns an answer, not the table.")
    except Exception:  # a guard failure must never block a tool call
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
