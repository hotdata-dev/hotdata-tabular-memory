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

You will be asked more questions about this same data in later sessions, and you will
not remember this one. Before you finish, record anything you had to work out that
would save you the work next time: a filter you had to define, a quirk of the schema or
the SQL dialect, a figure you may be asked for again. Use whatever memory this session
gives you. If it gives you none, skip this.

Questions:
{questions}
"""


DATA_DIR = "/private/tmp/claude-501/-Users-eddie-Code-claude-hotdata-plugin/6bc8e054-526c-4223-8763-8182b4f473d2/scratchpad/bench"

LOCAL_BRIEF = """You are answering questions about New York yellow taxi trips for January 2024.

The data is in two files:

    {data}/yellow_2024-01.parquet   one row per trip
    {data}/taxi_zone_lookup.csv     the LocationID lookup, with Borough and Zone

Answer each question below. Work however you like, with whatever is on this machine.

When you are done, print one final line and nothing after it:

ANSWERS {{"q01": <value>, "q02": <value>, ...}}

A value is a bare number (no dollar sign, no thousands separator) or a string, matching
what the question asks for. Use the question ids exactly as given.

Questions:
{questions}
"""

PLUGIN_BRIEF = """You are answering questions about New York yellow taxi trips for January 2024.

The project's data is in Hotdata and the `hd` command is on your PATH. Your session
started with the database, its tables, a sample, any definitions and the SQL dialect.
Use `hd query "<sql>"`, or `hd batch "label: <sql>" "label: <sql>" ...` to ask several
questions in one call. In SQL, {{{{name}}}} expands to a definition; `hd defs` lists them.
If a question names a set (such as "the clean set") and no definition exists, define it
from the question's own words with `hd define` before answering, so later questions use
the same one.

Answer each question below. When you are done, print one final line and nothing after it:

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
                      "Bash(hd:*)",
                      "Bash(memory recall:*)", "Bash(memory profile:*)",
                      "Bash(memory list:*)", "Bash(memory remember:*)",
                      "Bash(memory status)"],
        },
        "autoMemoryDirectory": str(home / "memory"),
    }
    if arm == "local":
        # Claude as it would be if Hotdata did not exist: the files are on disk, every
        # tool on the machine is fair game, and the CLI is denied so a stray mention in
        # the skill listing cannot turn into a call.
        base["permissions"] = {"allow": ["Bash", "Read", "Write", "Edit", "Glob", "Grep"],
                               "deny": ["Bash(hotdata:*)", "Bash(hd:*)"]}
        base["autoMemoryEnabled"] = False
        base["enabledPlugins"] = {"hotdata-memory@hotdata": False, "hotdata@hotdata": False}
    elif arm in ("bare", "plugin"):
        base["autoMemoryEnabled"] = False
        base["enabledPlugins"] = {"hotdata-memory@hotdata": False, "hotdata@hotdata": False}
    elif arm == "markdown":
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


PLUGIN_DIR = str(HERE.parent)


def plugin_env(arm: str, scope: str) -> dict:
    """Return the plugin option environment for `arm`."""
    if arm == "plugin":
        return {"CLAUDE_PLUGIN_OPTION_DATABASE": DB,
                "CLAUDE_PLUGIN_OPTION_PROJECT": "taxi",
                "CLAUDE_PLUGIN_OPTION_READ_GUARD": "on"}
    if arm != "hotdata":
        return {}
    return {
        "CLAUDE_PLUGIN_OPTION_STORE": "hotdata",
        "CLAUDE_PLUGIN_OPTION_MODE": "standalone",
        "CLAUDE_PLUGIN_OPTION_DATABASE_ID": MEMORY_DB,
        "CLAUDE_PLUGIN_OPTION_WORKSPACE_ID": WORKSPACE,
        "CLAUDE_PLUGIN_OPTION_SCOPE_PREFIX": scope,
        # Pin the project. The work directory is not a repository, so without this the
        # scope would follow the shell and split when the agent changes directory.
        "CLAUDE_PLUGIN_OPTION_PROJECT": "taxi",
        # Attach memory to the database being queried, so a session can read both in
        # one statement instead of shelling out to the memory command.
        "CLAUDE_PLUGIN_OPTION_WORK_DATABASE": DB,
        "CLAUDE_PLUGIN_OPTION_EMBEDDER": "hashed",
    }


ANSWER_LINE = re.compile(r"ANSWERS\s*(\{.*\})", re.DOTALL)


def parse_answers(text: str) -> dict:
    match = None
    for match in ANSWER_LINE.finditer(text):
        pass
    if match is None:
        return {}
    raw = match.group(1).strip()
    # A model that takes the brief literally can wrap the object in a second pair of
    # braces. Read what it meant rather than scoring the session zero.
    candidates = [raw]
    if raw.startswith("{{") and raw.endswith("}}"):
        candidates.insert(0, raw[1:-1])
    for text in candidates:
        for end in range(len(text), 0, -1):
            try:
                found = json.loads(text[:end])
            except json.JSONDecodeError:
                continue
            if isinstance(found, dict):
                return found
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
    parser.add_argument("--arm", required=True,
                        choices=["local", "bare", "plugin", "none", "markdown", "hotdata"])
    parser.add_argument("--replicate", type=int, required=True)
    parser.add_argument("--out", type=Path, default=HERE / "runs")
    parser.add_argument("--sessions", type=int, default=20)
    parser.add_argument("--model", default="claude-opus-5")
    parser.add_argument("--plugin-dir", default="",
                        help="load the plugin from this directory for the run")
    parser.add_argument("--tag", default="bench",
                        help="run label; keeps one run's memory out of the next one's")
    args = parser.parse_args()

    agent = f"{args.arm}-r{args.replicate}"
    root = args.out.resolve() / agent
    if root.exists():
        shutil.rmtree(root)
    (root / "home").mkdir(parents=True)
    home = root / "home"
    work = root / "work"
    work.mkdir()

    cli_version = subprocess.run(["claude", "--version"], capture_output=True,
                                 text=True).stdout.strip()
    questions = json.loads((HERE / "questions.json").read_text())
    scope = f"{args.tag}-{agent}"

    settings = home / "settings.json"
    settings.write_text(json.dumps(arm_settings(args.arm, home), indent=1))

    env = {**os.environ, **plugin_env(args.arm, scope)}
    env["HOTDATA_API_KEY"] = (Path.home() / ".hotdata/api-key").read_text().strip()
    env["CLAUDE_CODE_PROJECT_DIR_NAME"] = scope
    # A session started by another Claude Code session has auto memory off by default,
    # and `autoMemoryEnabled` cannot turn it back on there. Only "0" forces it on, so
    # every arm states what it wants rather than inheriting it.
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "0" if args.arm == "markdown" else "1"
    if args.arm != "local":
        env["PATH"] = f"{PLUGIN_DIR}/bin:" + env.get("PATH", "")
    # One plugin data dir per run, shared by every agent in it: a definition one agent
    # makes reaches the next one's orientation, which is the behaviour under test.
    env["CLAUDE_PLUGIN_DATA"] = str(args.out.resolve() / "plugin-data")

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
        brief = {"plugin": PLUGIN_BRIEF, "local": LOCAL_BRIEF}.get(args.arm, BRIEF)
        prompt = brief.format(db=DB, data=DATA_DIR, questions=listing)
        started = time.time()
        proc = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json",
             "--model", args.model, "--settings", str(settings),
             "--permission-mode", "acceptEdits",
             *(["--plugin-dir", args.plugin_dir or PLUGIN_DIR]
               if (args.plugin_dir or args.arm == "plugin") else [])],
            capture_output=True, text=True, cwd=work, env=env, timeout=3600,
        )
        wall = time.time() - started
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError:
            result = {"is_error": True, "result": proc.stdout[-2000:],
                      "stderr": proc.stderr[-2000:]}
        result["wall_seconds"] = wall
        result["cli_version"] = cli_version
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
