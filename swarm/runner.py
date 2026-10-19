"""Run the swarm: one agent per task, C agents at a time, in one arm.

Every task needs complaints classified. What differs between arms is whether an
agent can find and reuse the classifications other agents already made:

  isolated  files on disk, nothing shared. How agents work today.
  folder    the same files, plus one folder every agent can read and write.
  hotdata   the plugin, one project database with a `labels` table every agent shares.

Each session is a separate `claude -p` process with a fresh working directory. Its
JSON result goes to <run>/sessions/<task>.json; after the run, every label any agent
recorded is collected into <run>/labels.jsonl, and the graded answers into
<run>/answers.jsonl.

  python3 runner.py --arm hotdata --concurrency 4 --replicate 1 --tasks 20
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import duckdb

from gen_complaints import MEANING

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
PLUGIN_DIR = str(HERE.parent)
# Agents work outside the repository, where the truth and every other run live. Their
# data directory holds only the three files they are meant to see.
BENCH = Path.home() / "swarm-bench"
AGENT_DATA = BENCH / "data"
SOURCES = {"trips": "trips.parquet", "complaints": "complaints.parquet",
           "zones": "taxi_zone_lookup.csv"}
FILES = {k: AGENT_DATA / v for k, v in SOURCES.items()}

BRIEF = """You are one of many agents working for a city taxi commission. Passengers file
complaints about taxi trips, one trip per complaint. Your job is to answer one question
that needs the complaints on some trips classified by their main issue.

Categories, by these exact names:
{categories}

A complaint's category is the passenger's main reason for complaining. A minor
annoyance mentioned in passing is not.

{store}

Keep any scratch files in your working directory.

Record every classification you make, with your agent id `{agent}`, so the work is not
lost: {record}
{claiming}
Question: {ask}

When you are done, print one final line and nothing after it:

ANSWER <value>

The value is a bare number (no units, no thousands separator) or a category name.
"""

FILES_STORE = """The data is in files:

    {trips}   one row per trip, keyed by trip_id
    {complaints}   complaint_id, trip_id, text
    {zones}   the LocationID lookup, with Borough and Zone

Work however you like, with whatever is on this machine."""

STORES = {
    "isolated": (
        FILES_STORE,
        "append them to labels.csv in your working directory, with the columns "
        "complaint_id,category,agent."),
    "folder": (
        FILES_STORE + """

Other agents working on related questions share the folder {shared}. The
classifications they have made are CSV files in {shared}/labels/ with the columns
complaint_id, category, agent. Use them rather than classifying a complaint again.""",
        "write them as CSV into {shared}/labels/ with the columns complaint_id,category,"
        "agent. Other agents read that folder, possibly while you write to it."),
    "hotdata": (
        """The data is in the project's Hotdata database, and `tabular-memory` is on your PATH. Your
session started with its tables, a sample of each and the SQL dialect. The tables:

    {cat}.public.trips        one row per trip, keyed by trip_id
    {cat}.public.complaints   complaint_id, trip_id, text
    {cat}.public.zones        the LocationID lookup, with Borough and Zone
    {cat}.public.labels       complaint_id, category, agent
    {cat}.public.claims       complaint_id, agent: work in progress

`labels` holds the classifications other agents working on related questions have
made. Use them rather than classifying a complaint again.""",
        "INSERT them into {cat}.public.labels with `tabular-memory query`, all of them in one "
        "statement. If the insert is refused with \"retry shortly\", another agent is "
        "writing; run the same statement again after a few seconds."),
}

CLAIMING = """
Other agents are working at the same time, often on the same complaints. Before you
classify, claim the work so two agents never classify the same complaint:

1. Find the complaints your question needs that have no label yet.
2. Claim the ones nobody has claimed: {claim}
3. Classify exactly the complaints you claimed, and record them as above.
4. A complaint another agent claimed but has not labelled yet is being classified
   now. Wait about 30 seconds and check the labels again. If one is still missing after
   five minutes, classify it yourself.
"""

CLAIMS = {
    "folder": "create the empty file {shared}/claims/<complaint_id> for each one, "
              "exclusively, so creating it fails if it already exists (in Python, "
              "open(path, 'x')). The files you created are your claims.",
    "hotdata": "in one statement, INSERT INTO {cat}.public.claims (complaint_id, agent) "
               "SELECT the unlabelled, unclaimed ids with '{agent}' as the agent, excluding "
               "complaint_id IN (SELECT complaint_id FROM {cat}.public.claims). Your claims "
               "are the rows with agent = '{agent}'.",
}

ANSWER = re.compile(r"^ANSWER\s+(.+?)\s*$", re.M)


def settings_for(arm: str) -> dict:
    """Every arm gets every tool on the machine; only where the data lives differs.

    The arms without Hotdata have the CLI denied, so a mention of it in a skill listing
    cannot turn into a call.
    """
    permissions = {"allow": ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]}
    if arm != "hotdata":
        permissions["deny"] = ["Bash(hotdata:*)", "Bash(tabular-memory:*)"]
    return {"permissions": permissions, "autoMemoryEnabled": False,
            "enabledPlugins": {"hotdata-tabular-memory@hotdata": False}}


def hotdata(*args: str) -> str:
    run = subprocess.run(["hotdata", *args, "--no-input"], capture_output=True, text=True)
    if run.returncode:
        raise SystemExit(f"hotdata {' '.join(args)}: {run.stderr.strip()}")
    return run.stdout


def make_database(name: str, scratch: Path) -> tuple[str, str]:
    """A fresh database for one run: the three data tables and an empty labels table."""
    catalog = f"swarm_{name.replace('-', '_')}_{secrets.token_hex(3)}"
    made = json.loads(hotdata("databases", "create", "--name", f"swarm {name}",
                              "--catalog", catalog, "--expires-at", "7d", "-o", "json"))
    db = made["id"]
    for table, path in FILES.items():
        hotdata("databases", "load", "--catalog", catalog, "--table", table, "--file", str(path))
    # A typed parquet with no rows: the server has no DELETE yet, so the table cannot
    # be seeded with a row and emptied.
    seed = scratch / "labels_empty.parquet"
    duckdb.execute(f"COPY (SELECT 0::INTEGER AS complaint_id, ''::VARCHAR AS category, "
                   f"''::VARCHAR AS agent WHERE false) TO '{seed}' (FORMAT parquet)")
    hotdata("databases", "load", "--catalog", catalog, "--table", "labels", "--file", str(seed))
    claims = scratch / "claims_empty.parquet"
    duckdb.execute(f"COPY (SELECT 0::INTEGER AS complaint_id, ''::VARCHAR AS agent "
                   f"WHERE false) TO '{claims}' (FORMAT parquet)")
    hotdata("databases", "load", "--catalog", catalog, "--table", "claims", "--file", str(claims))
    return db, catalog


def labels_from_csv(text: str, source: str, bad: list) -> list[dict]:
    out = []
    try:
        for row in csv.DictReader(io.StringIO(text)):
            try:
                out.append({"complaint_id": int(row["complaint_id"]),
                            "category": (row.get("category") or "").strip(),
                            "agent": (row.get("agent") or "").strip(), "source": source})
            except (KeyError, TypeError, ValueError):
                bad.append({"source": source, "row": row})
    except csv.Error as error:
        bad.append({"source": source, "error": str(error)})
    return out


def collect_labels(arm: str, root: Path, db: str, catalog: str) -> tuple[list, list]:
    bad: list = []
    if arm == "hotdata":
        text = hotdata("query", f"SELECT complaint_id, category, agent FROM "
                                f"{catalog}.public.labels", "-d", db, "-o", "csv")
        return labels_from_csv(text, "labels", bad), bad
    files = (sorted((root / "sessions").glob("*/labels.csv")) if arm == "isolated"
             else sorted((root / "shared" / "labels").rglob("*")))
    labels = []
    for f in files:
        if f.is_file():
            labels += labels_from_csv(f.read_text(errors="replace"), str(f.relative_to(root)), bad)
    return labels, bad


def grade(task: dict, given: str | None) -> bool:
    if given is None:
        return False
    if task["kind"] == "string":
        return given.strip().strip("`'\"").lower() == task["answer"]
    try:
        value = float(given.replace(",", "").replace("%", "").replace("$", "").strip())
    except ValueError:
        return False
    truth, tol = task["answer"], task["tol"]
    if task["kind"] == "number":
        return abs(value - truth) <= tol
    if task["kind"] == "count":
        return abs(value - truth) <= max(2, tol * truth)
    return abs(value - truth) <= tol * abs(truth)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", required=True, choices=list(STORES))
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--replicate", type=int, default=1)
    parser.add_argument("--tasks", type=int, default=0, help="run only the first N tasks")
    parser.add_argument("--pick", default="", help="run only these task ids, in this order")
    parser.add_argument("--model", default="claude-opus-5")
    parser.add_argument("--out", type=Path, default=BENCH / "runs")
    parser.add_argument("--claim", action="store_true",
                        help="shared arms claim complaints before classifying them")
    args = parser.parse_args()

    AGENT_DATA.mkdir(parents=True, exist_ok=True)
    for f in SOURCES.values():
        if not (AGENT_DATA / f).exists():
            shutil.copy(DATA / f, AGENT_DATA / f)
    name = f"{args.arm}-c{args.concurrency}-r{args.replicate}" + ("-claim" if args.claim else "")
    root = args.out.resolve() / name
    if root.exists():
        shutil.rmtree(root)
    (root / "sessions").mkdir(parents=True)
    tasks = json.loads((HERE / "tasks.json").read_text())
    if args.pick:
        by_id = {t["id"]: t for t in tasks}
        tasks = [by_id[i] for i in args.pick.split(",")]
    if args.tasks:
        tasks = tasks[:args.tasks]

    settings = root / "settings.json"
    settings.write_text(json.dumps(settings_for(args.arm), indent=1))
    env = {**os.environ, "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
    db = catalog = ""
    shared = root / "shared"
    if args.arm == "folder":
        (shared / "labels").mkdir(parents=True)
        (shared / "claims").mkdir()
    if args.arm == "hotdata":
        db, catalog = make_database(name, root)
        env["HOTDATA_API_KEY"] = (Path.home() / ".hotdata/api-key").read_text().strip()
        env["PATH"] = f"{PLUGIN_DIR}/bin:" + env.get("PATH", "")
        env["CLAUDE_PLUGIN_OPTION_DATABASE"] = db
        env["CLAUDE_PLUGIN_OPTION_PROJECT"] = f"swarm-{name}"
        env["CLAUDE_PLUGIN_DATA"] = str(root / "plugin-data")
    cli_version = subprocess.run(["claude", "--version"], capture_output=True,
                                 text=True).stdout.strip()
    (root / "run.json").write_text(json.dumps({
        "arm": args.arm, "concurrency": args.concurrency, "replicate": args.replicate,
        "model": args.model, "cli": cli_version, "database": db, "catalog": catalog,
        "claim": args.claim,
        "tasks": [t["id"] for t in tasks]}, indent=1))

    categories = "\n".join(f"- {k}: {v}" for k, v in MEANING.items())
    store, record = STORES[args.arm]
    paths = {k: str(v) for k, v in FILES.items()}
    lock = threading.Lock()
    run_started = time.time()

    halted = threading.Event()

    def session(task: dict) -> None:
        if halted.is_set():
            return
        work = root / "sessions" / task["id"]
        work.mkdir()
        fill = {**paths, "shared": str(shared), "cat": catalog}
        fill["agent"] = task["id"]
        claiming = (CLAIMING.format(claim=CLAIMS[args.arm].format(**fill))
                    if args.claim and args.arm in CLAIMS else "")
        prompt = BRIEF.format(categories=categories, store=store.format(**fill),
                              record=record.format(**fill), claiming=claiming,
                              agent=task["id"], ask=task["ask"])
        started = time.time()
        (work / "tmp").mkdir()
        proc = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json", "--model", args.model,
             "--settings", str(settings), "--permission-mode", "acceptEdits",
             *(["--plugin-dir", PLUGIN_DIR] if args.arm == "hotdata" else [])],
            capture_output=True, text=True, cwd=work, env={**env, "TMPDIR": str(work / "tmp")},
            timeout=3600)
        ended = time.time()
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError:
            result = {"is_error": True, "result": proc.stdout[-2000:], "stderr": proc.stderr[-2000:]}
        if result.get("is_error") and "limit" in str(result.get("result", "")).lower():
            # Every later session would fail the same way; a run with holes in it
            # cannot be compared, so stop and say so.
            halted.set()
            print(f"{name} HALTED at {task['id']}: {str(result.get('result'))[:120]}", flush=True)
        found = ANSWER.findall(result.get("result") or "")
        given = found[-1] if found else None
        result.update({"task": task["id"], "start": started - run_started,
                       "end": ended - run_started, "given": given,
                       "correct": grade(task, given), "cli_version": cli_version})
        (root / "sessions" / f"{task['id']}.json").write_text(json.dumps(result, indent=1))
        with lock:
            with (root / "answers.jsonl").open("a") as out:
                out.write(json.dumps({"task": task["id"], "given": given,
                                      "answer": task["answer"], "correct": result["correct"]}) + "\n")
            print(f"{name} {task['id']} {'ok ' if result['correct'] else 'BAD'} "
                  f"given={given} truth={task['answer']} ${result.get('total_cost_usd', 0):.2f} "
                  f"{ended - started:.0f}s turns={result.get('num_turns', '?')}", flush=True)

    with ThreadPoolExecutor(args.concurrency) as pool:
        list(pool.map(session, tasks))

    labels, bad = collect_labels(args.arm, root, db, catalog)
    with (root / "labels.jsonl").open("w") as out:
        for label in labels:
            out.write(json.dumps(label) + "\n")
    (root / "unreadable.json").write_text(json.dumps(bad, indent=1))
    correct = sum(json.loads(l)["correct"] for l in (root / "answers.jsonl").read_text().splitlines())
    print(f"{name} DONE {correct}/{len(tasks)} correct, {len(labels)} labels recorded, "
          f"{len(bad)} unreadable", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
