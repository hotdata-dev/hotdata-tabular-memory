"""SessionStart hook: load this project's memory from the store into the session.

A session must not wait on the network, so the block that the last run cached is
printed and a refresh runs in the background. The first run for a project has no
cache, so it reads the store once and waits.

Run with `--refresh` to write the cache and print nothing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import bridge
from hotmemory import Filter
from hotmemory.contract import RECALLED_KINDS, fit_lines, render_line

HEADING = "Memory for this project, from Hotdata (hotmemory). Records, not rules."
EMPTY_HEADING = "No memory is recorded for this project yet."
STANDALONE_HEAD = """
This project keeps no memory files. The store above is the only memory, so a fact worth
keeping must be written to it before this session ends, or it is lost:

    memory remember "<the fact>" --subject <slug> --kind fact|profile|procedure

Record how to get an answer, not the answer. A schema detail, a dialect quirk, a
definition you were given, a command that worked: these stay true and save the next
session real work. A figure you computed does not belong here on its own — it was true of
one query on one day, and a wrong one recorded as fact is worse than no memory, because
every later answer inherits it. To keep a figure, keep what reproduces it:

    memory remember "<the fact>" --subject <slug> --query "<the SQL that returns it>"

A write carrying several figures and no --query is refused, and so is one that nearly
repeats a stored record and disagrees with it. When a record turns out wrong, correct it:

    memory supersede <record id> "<the correct fact>"

Never label your own conclusion canonical, settled or verified. When a record disagrees
with the data, the data is right: re-run the query and supersede the record."""

STANDALONE_SQL = """

Memory is also a table in the database you are querying, so you can read it in the same
query as the data, without a second command:

    {sql}

`payload` carries the query that reproduces a recorded figure, when one was given.
Prefer re-running that query over trusting the figure beside it."""


LIMIT = 200


def newest_first(record) -> tuple:
    return (-record.created_at.timestamp(), record.id)


def block(scope: tuple[str, ...], budget: int) -> str:
    """Return the memory block for `scope`, or an empty string if there is nothing to say.

    In standalone mode the store is the only memory, so the block always carries the
    instructions for writing to it, even when no record exists yet.
    """
    standalone = bridge.mode() == "standalone"
    footer = ""
    if standalone:
        footer = STANDALONE_HEAD
        alias = bridge.ensure_attached()
        if alias:
            footer += STANDALONE_SQL.format(sql=bridge.memory_sql(scope, alias))
    where = Filter(kind=RECALLED_KINDS)
    with bridge.memory() as memory:
        records = sorted(memory.store.list(scope, where, limit=LIMIT), key=newest_first)
    room = budget - len(footer)
    if not records:
        return f"{EMPTY_HEADING}{footer}" if standalone else ""
    lines = [render_line(record) for record in records]
    kept = lines[: fit_lines(lines, room - len(HEADING) - 1)]
    if not kept:
        return f"{EMPTY_HEADING}{footer}" if standalone else ""
    return "\n".join([HEADING, *kept]) + footer


def refresh(scope: tuple[str, ...]) -> str:
    """Read the store and replace the cached block of `scope`. Returns the block."""
    text = block(scope, bridge.budget())
    bridge.write_cache(scope, text)
    return text


def spawn_refresh(scope: tuple[str, ...], cwd: str) -> None:
    """Start a refresh that outlives this hook, and never raise."""
    try:
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--refresh", "--cwd", cwd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


def emit(text: str) -> None:
    if not text:
        return
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": text,
                }
            }
        )
    )


def main(argv: list[str]) -> int:
    if "--refresh" in argv:
        cwd = argv[argv.index("--cwd") + 1] if "--cwd" in argv else os.getcwd()
        try:
            refresh(bridge.scope_for(cwd))
        except Exception:
            pass
        return 0

    payload = json.loads(sys.stdin.read() or "{}")
    cwd = payload.get("cwd", "")
    scope = bridge.scope_for(cwd)
    path = bridge.cache_path(scope)
    if path.exists():
        emit(path.read_text(encoding="utf-8"))
        spawn_refresh(scope, cwd)
        return 0
    try:
        emit(refresh(scope))
    except Exception as error:  # a memory failure must never stop a session
        print(json.dumps({"systemMessage": f"hotdata-memory: {error}"}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
