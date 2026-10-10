"""PostToolUse hook: mirror a written Claude Code memory note into the store.

Reads the hook payload on stdin. If the file written is a memory note, the note becomes
one fact under the project scope, and the cached session-start block is rewritten so the
next session sees it. Writing the same note again writes nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import bridge
import session_start


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    raw = (payload.get("tool_input") or {}).get("file_path", "")
    if not raw:
        return 0
    path = Path(raw)
    if not bridge.is_memory_file(path) or not path.exists():
        return 0
    try:
        text = path.read_text(encoding="utf-8")
        fact = bridge.note_to_fact(path, text, payload.get("session_id", ""))
        if fact is None:
            return 0
        scope = bridge.scope_for(payload.get("cwd", ""))
        with bridge.memory(write=True) as memory:
            memory.remember([fact], scope, actor="claude-code")
        session_start.refresh(scope)
    except Exception as error:  # a memory failure must never block an edit
        print(json.dumps({"systemMessage": f"hotdata-memory: {error}"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
