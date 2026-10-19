"""SessionStart hook: tell the agent about the project's database before it asks.

Prints the cached orientation block and refreshes it in the background. Before that,
on a machine not yet set up, it says the one thing to fix; the first session that
finds everything working says hello once. A project with no database hears only
about data files it has that are too big to read. A failure never stops a session.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import guard
import orient
import project
import setup


def emit(text: str, notice: str = "") -> None:
    """Print `text` for the agent and `notice` for the user, either one optional."""
    out: dict = {}
    if notice:
        out["systemMessage"] = notice
    if text:
        out["hookSpecificOutput"] = {"hookEventName": "SessionStart", "additionalContext": text}
    if out:
        print(json.dumps(out))


def main(argv: list[str]) -> int:
    if "--refresh" in argv:
        cwd = argv[argv.index("--cwd") + 1] if "--cwd" in argv else os.getcwd()
        try:
            entry = project.project_database(cwd)
            if entry:
                orient.refresh(entry, orient.budget())
        except Exception:
            pass
        return 0
    payload = json.loads(sys.stdin.read() or "{}")
    cwd = payload.get("cwd", "")
    try:
        problem, welcome = setup.session_check()
        if problem:
            emit(f"The tabular-memory plugin cannot run yet: {problem} Tell the user if "
                 f"they ask for data work; do not try to fix it yourself.",
                 f"Hotdata Tabular Memory: {problem}")
            return 0
        entry = project.project_database(cwd)
        if not entry:
            emit(setup.unloaded_hint(cwd, guard.threshold_bytes()), welcome)
            return 0
        text = orient.cached(entry)
        if text is not None:
            emit(text, welcome)
            subprocess.Popen(
                [sys.executable, os.path.abspath(__file__), "--refresh", "--cwd", cwd],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, start_new_session=True)
            return 0
        emit(orient.refresh(entry, orient.budget()), welcome)
    except Exception as error:
        print(json.dumps({"systemMessage": f"hotdata: {error}"}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
