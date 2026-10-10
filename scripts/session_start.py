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
STANDALONE = """
This project keeps no memory files. The store above is the only memory, so a fact worth
keeping must be written to it before this session ends, or it is lost:

    memory remember "<the fact>" --subject <slug> --kind fact|profile|procedure

Use `fact` for something about the project, `profile` for something about the person, and
`procedure` for how to work. Run `memory recall "<question>"` to search for more than what
loaded above. Record what you could not derive again cheaply: a schema quirk you had to
discover, a decision and its reason, a correction the person gave you. Writing the same
content twice writes nothing, so a repeat is safe."""
LIMIT = 200


def newest_first(record) -> tuple:
    return (-record.created_at.timestamp(), record.id)


def block(scope: tuple[str, ...], budget: int) -> str:
    """Return the memory block for `scope`, or an empty string if there is nothing to say.

    In standalone mode the store is the only memory, so the block always carries the
    instructions for writing to it, even when no record exists yet.
    """
    standalone = bridge.mode() == "standalone"
    footer = STANDALONE if standalone else ""
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
