"""PostToolUse hook: mirror a written Claude Code memory note into the store.

Only used in mirror mode, where Claude's own memory files stay the source of truth
and the store follows them.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import bridge
import session_start
from store import Record

FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.DOTALL)
KIND_OF_TYPE = {"user": "profile", "feedback": "procedure",
                "project": "fact", "reference": "fact"}


def memory_roots() -> list[Path]:
    override = bridge.option("memory_root")
    if override:
        return [Path(override).expanduser()]
    return [Path.home() / ".claude" / "projects"]


def is_memory_file(path: Path) -> bool:
    """MEMORY.md is the index Claude rewrites from the notes, so it is not a note."""
    if path.suffix != ".md" or path.name == "MEMORY.md" or "memory" not in path.parts:
        return False
    for root in memory_roots():
        try:
            path.resolve().relative_to(root.resolve())
        except (OSError, ValueError):
            continue
        return True
    return False


def parse_note(text: str) -> dict[str, str]:
    """Return the name, description, type and body of a memory note."""
    match = FRONTMATTER.match(text)
    if not match:
        return {"name": "", "description": "", "type": "project", "body": text.strip()}
    found: dict[str, str] = {}
    section = ""
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indented = line[0] in " \t"
        key, _, value = line.strip().partition(":")
        key, value = key.strip(), value.strip().strip("'\"")
        if not indented:
            section = key if not value else ""
            if value:
                found[key] = value
        elif section:
            found[f"{section}.{key}"] = value
    kind = found.get("metadata.type", "project")
    return {"name": found.get("name", ""),
            "description": found.get("description", ""),
            "type": kind if kind in KIND_OF_TYPE else "project",
            "body": match.group(2).strip()}


def note_to_record(path: Path, text: str, namespace: str, session: str) -> Record | None:
    note = parse_note(text)
    if not note["body"]:
        return None
    content = (f"{note['description']}\n\n{note['body']}" if note["description"]
               else note["body"])
    sources = ",".join(filter(None, [f"file:{path.name}",
                                     f"session:{session}" if session else ""]))
    return Record(namespace=namespace, subject=note["name"] or path.stem,
                  kind=KIND_OF_TYPE[note["type"]], content=content,
                  tags=f"type:{note['type']}", sources=sources, actor="claude-code")


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    raw = (payload.get("tool_input") or {}).get("file_path", "")
    if not raw or bridge.option("mode", "mirror") != "mirror":
        return 0
    path = Path(raw)
    if not is_memory_file(path) or not path.exists():
        return 0
    try:
        namespace = bridge.namespace_for(payload.get("cwd", ""))
        record = note_to_record(path, path.read_text(encoding="utf-8"), namespace,
                                payload.get("session_id", ""))
        if record is None:
            return 0
        bridge.store().write([record])
        session_start.refresh(namespace)
    except Exception as error:  # a memory failure must never block an edit
        print(json.dumps({"systemMessage": f"hotdata-memory: {error}"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
