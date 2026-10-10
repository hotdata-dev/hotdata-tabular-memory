"""SessionStart hook: tell the agent about the project's database before it asks.

Prints the cached orientation block and refreshes it in the background. A project
with no database gets nothing. A failure never stops a session.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import orient
import project


def emit(text: str) -> None:
    if text:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "SessionStart", "additionalContext": text}}))


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
        entry = project.project_database(cwd)
        if not entry:
            return 0
        text = orient.cached(entry)
        if text is not None:
            emit(text)
            subprocess.Popen(
                [sys.executable, os.path.abspath(__file__), "--refresh", "--cwd", cwd],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, start_new_session=True)
            return 0
        emit(orient.refresh(entry, orient.budget()))
    except Exception as error:
        print(json.dumps({"systemMessage": f"hotdata: {error}"}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
