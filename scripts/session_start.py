"""SessionStart hook: put this project's memory into the session.

A session must not wait on the network, so the block the last run cached is printed
and a refresh runs in the background. The first run for a project has no cache and
reads once.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import bridge

HEADING = "Memory for this project, from Hotdata. Records, not rules."
EMPTY = "No memory is recorded for this project yet."
HOWTO = """
This project keeps no memory files. The store is the only memory, so a fact worth
keeping must be written to it before this session ends, or it is lost:

    memory remember "<the fact>" --subject <slug> --kind fact|profile|procedure

Record how to get an answer, not the answer. A schema detail, a dialect quirk, a
definition you were given, a command that worked: these stay true and save the next
session real work. A figure you computed does not belong here on its own. To keep one,
keep what reproduces it:

    memory remember "<the fact>" --subject <slug> --query "<the SQL that returns it>"

A write carrying several figures and no --query is refused, and so is a second record
on a subject that already has one saying something else. Correct a record instead:

    memory supersede <record id> "<the correct fact>"

Never label your own conclusion canonical, settled or verified. When a record disagrees
with the data, the data is right: re-run the query and supersede the record."""

SQL_NOTE = """

Memory is a table in the database you are querying, so one statement reads both:

    {sql}
"""


def block(namespace: str, budget: int) -> str:
    """Return the memory block for `namespace`, or an empty string if there is none."""
    footer = HOWTO
    store = bridge.store()
    if getattr(store, "database", None):
        footer += SQL_NOTE.format(sql=bridge.memory_sql(namespace, store.catalog))
    records = store.current(namespace)
    if not records:
        return EMPTY + footer
    lines = bridge.fit([r.line() for r in records],
                       budget - len(HEADING) - len(footer) - 2)
    if not lines:
        return EMPTY + footer
    return "\n".join([HEADING, *lines]) + footer


def refresh(namespace: str) -> str:
    text = block(namespace, bridge.budget())
    bridge.write_cache(namespace, text)
    return text


def spawn_refresh(cwd: str) -> None:
    """Start a refresh that outlives this hook, and never raise."""
    try:
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--refresh", "--cwd", cwd],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )
    except Exception:
        pass


def emit(text: str) -> None:
    if not text:
        return
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart", "additionalContext": text}}))


def main(argv: list[str]) -> int:
    if "--refresh" in argv:
        cwd = argv[argv.index("--cwd") + 1] if "--cwd" in argv else os.getcwd()
        try:
            refresh(bridge.namespace_for(cwd))
        except Exception:
            pass
        return 0

    payload = json.loads(sys.stdin.read() or "{}")
    cwd = payload.get("cwd", "")
    namespace = bridge.namespace_for(cwd)
    path = bridge.cache_path(namespace)
    if path.exists():
        emit(path.read_text(encoding="utf-8"))
        spawn_refresh(cwd)
        return 0
    try:
        emit(refresh(namespace))
    except Exception as error:  # a memory failure must never stop a session
        print(json.dumps({"systemMessage": f"hotdata-memory: {error}"}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
