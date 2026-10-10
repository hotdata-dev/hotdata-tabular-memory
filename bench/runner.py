"""Run one benchmark agent: ten sessions of five questions, in one arm.

Every session is a separate `claude -p` process, so memory has to cross a session
boundary to be worth anything. The arms differ only in how memory is kept; the data,
the questions, and their order are identical.

Each session's JSON result is written to <out>/<agent>/sNN.json, and the graded answers
to <out>/<agent>/answers.jsonl.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = "dbid_redacted_benchmark"
MEMORY_DB = "dbid_redacted_memory"
WORKSPACE = "work_redacted"
PER_SESSION = 5

BRIEF = """You are answering questions about New York yellow taxi trips for January 2024.

The data is in a Hotdata managed database. Query it with:

    hotdata query "<SQL>" -d {db} -o csv

Two tables: `taxi.public.trips` (one row per trip) and `taxi.public.zones` (the
LocationID lookup, with Borough and Zone). Column names are case-sensitive; wrap the
uppercase ones in double quotes, for example "PULocationID".

Answer each question below. Work however you like. When you are done, print one final
line and nothing after it:

ANSWERS {{"q01": <value>, "q02": <value>, ...}}

A value is a bare number (no dollar sign, no thousands separator) or a string, matching
what the question asks for. Use the question ids exactly as given.

Questions:
{questions}
"""


def arm_settings(arm: str, home: Path) -> dict:
    """Return the settings.json contents for `arm`."""
    base = {
        "permissions": {
            "allow": ["Bash(hotdata query:*)", "Bash(hotdata databases:*)",
                      "Bash(memory recall:*)", "Bash(memory profile:*)",
                      "Bash(memory list:*)", "Bash(memory remember:*)",
                      "Bash(memory status)"],
        },
        "autoMemoryDirectory": str(home / "memory"),
    }
    if arm == "markdown":
        base["autoMemoryEnabled"] = True
        base["enabledPlugins"] = {"hotdata-memory@hotdata": False}
    elif arm == "hotdata":
        base["autoMemoryEnabled"] = False
        base["enabledPlugins"] = {"hotdata-memory@hotdata": True}
    elif arm == "none":
        base["autoMemoryEnabled"] = False
        base["enabledPlugins"] = {"hotdata-memory@hotdata": False}
    else:
        raise SystemExit(f"unknown arm {arm!r}")
    return base


def plugin_env(arm: str, scope: str) -> dict:
    """Return the plugin option environment for `arm`."""
    if arm != "hotdata":
        return {}
    return {
        "CLAUDE_PLUGIN_OPTION_STORE": "hotdata",
        "CLAUDE_PLUGIN_OPTION_MODE": "standalone",
        "CLAUDE_PLUGIN_OPTION_DATABASE_ID": MEMORY_DB,
        "CLAUDE_PLUGIN_OPTION_WORKSPACE_ID": WORKSPACE,
        "CLAUDE_PLUGIN_OPTION_SCOPE_PREFIX": scope,
        "CLAUDE_PLUGIN_OPTION_EMBEDDER": "hashed",
    }


ANSWER_LINE = re.compile(r"ANSWERS\s*(\{.*\})", re.DOTALL)


def parse_answers(text: str) -> dict:
    match = None
    for match in ANSWER_LINE.finditer(text):
        pass
    if match is None:
        return {}
    raw = match.group(1)
    for end in range(len(raw), 0, -1):
        try:
            return json.loads(raw[:end])
        except json.JSONDecodeError:
            continue
    return {}


def grade(question: dict, given) -> bool:
    if given is None:
        return False
    if question["kind"] == "number":
        try:
            value = float(str(given).replace("$", "").replace(",", "").strip())
        except (TypeError, ValueError):
            return False
        return abs(value - question["answer"]) <= max(question["tol"], 0.005)
    return str(given).strip().lower() == str(question["answer"]).strip().lower()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", required=True, choices=["markdown", "hotdata", "none"])
    parser.add_argument("--replicate", type=int, required=True)
    parser.add_argument("--out", type=Path, default=HERE / "runs")
    parser.add_argument("--sessions", type=int, default=20)
    parser.add_argument("--model", default="claude-opus-5")
    args = parser.parse_args()

    agent = f"{args.arm}-r{args.replicate}"
    root = args.out.resolve() / agent
    if root.exists():
        shutil.rmtree(root)
    (root / "home").mkdir(parents=True)
    home = root / "home"
    work = root / "work"
    work.mkdir()

    questions = json.loads((HERE / "questions.json").read_text())
    scope = f"bench-{agent}"

    settings = home / "settings.json"
    settings.write_text(json.dumps(arm_settings(args.arm, home), indent=1))

    env = {**os.environ, **plugin_env(args.arm, scope)}
    env["HOTDATA_API_KEY"] = (Path.home() / ".hotdata/api-key").read_text().strip()
    env["CLAUDE_CODE_PROJECT_DIR_NAME"] = scope
    if args.arm == "none":
        env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"

    answers_path = root / "answers.jsonl"
    graded = []
    per_pass = (len(questions) + PER_SESSION - 1) // PER_SESSION
    for index in range(args.sessions):
        # Pass 2 repeats pass 1's questions in the same order. An agent whose memory
        # works should answer them without querying the data again.
        slot = index % per_pass
        pass_no = index // per_pass + 1
        batch = questions[slot * PER_SESSION : (slot + 1) * PER_SESSION]
        if not batch:
            break
        listing = "\n".join(f"{q['id']}. {q['ask']}" for q in batch)
        prompt = BRIEF.format(db=DB, questions=listing)
        started = time.time()
        proc = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json",
             "--model", args.model, "--settings", str(settings),
             "--permission-mode", "acceptEdits"],
            capture_output=True, text=True, cwd=work, env=env, timeout=3600,
        )
        wall = time.time() - started
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError:
            result = {"is_error": True, "result": proc.stdout[-2000:],
                      "stderr": proc.stderr[-2000:]}
        result["wall_seconds"] = wall
        result["session_index"] = index
        result["pass"] = pass_no
        result["question_ids"] = [q["id"] for q in batch]
        (root / f"s{index:02d}.json").write_text(json.dumps(result, indent=1))

        given = parse_answers(result.get("result", "") or "")
        for q in batch:
            ok = grade(q, given.get(q["id"]))
            graded.append(ok)
            with answers_path.open("a") as handle:
                handle.write(json.dumps({
                    "session": index, "pass": pass_no, "id": q["id"],
                    "phase": q["phase"],
                    "given": given.get(q["id"]), "answer": q["answer"], "correct": ok,
                }) + "\n")
        score = sum(1 for q in batch if grade(q, given.get(q["id"])))
        print(f"{agent} s{index:02d} p{pass_no} {score}/{len(batch)} "
              f"${result.get('total_cost_usd', 0):.2f} {wall:.0f}s "
              f"turns={result.get('num_turns', '?')}", flush=True)

    print(f"{agent} DONE {sum(graded)}/{len(graded)} correct", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
