"""Mapping between Claude Code memory files and hotmemory records, and the store factory.

A Claude Code memory file is one Markdown file with YAML frontmatter:

    ---
    name: <slug>
    description: <one line>
    metadata:
      type: user | feedback | project | reference
    ---

    <body>

Each file becomes one hotmemory `Fact`. The body is the content, the slug is the
subject, and the Claude type becomes both the hotmemory `kind` and a `type:` tag.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import zlib
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from typing import Any

from hotmemory import Fact, Memory, MemoryStore, Record

HASHED_DIMENSIONS = 256
HASHED_MODEL = "hashed-words-256"

# Claude Code's memory types, mapped onto hotmemory's four kinds. A fact about the
# person is a profile; guidance on how to work is a procedure; the rest are facts.
KIND_OF_TYPE = {
    "user": "profile",
    "feedback": "procedure",
    "project": "fact",
    "reference": "fact",
}
DEFAULT_TYPE = "project"

TIME_FIELDS = (
    "created_at",
    "observed_at",
    "valid_from",
    "valid_until",
    "expired_at",
    "forget_after",
)

FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.DOTALL)


# --------------------------------------------------------------------------- config


def option(name: str, default: str = "") -> str:
    """Return a plugin option, which Claude Code exports as CLAUDE_PLUGIN_OPTION_<KEY>."""
    value = os.environ.get(f"CLAUDE_PLUGIN_OPTION_{name.upper()}", "").strip()
    return value or default


def data_dir() -> Path:
    """Return the directory that survives plugin updates, and make it."""
    root = os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.expanduser("~/.claude/hotdata-memory")
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def credentials() -> None:
    """Put the Hotdata API key and workspace in the environment, if they are missing.

    A hook does not inherit a login shell, so the key comes from the `api_key` option,
    then from the environment, then from the file the `hotdata` CLI writes.
    """
    key = option("api_key") or os.environ.get("HOTDATA_API_KEY", "")
    if not key:
        stored = Path.home() / ".hotdata" / "api-key"
        if stored.exists():
            key = stored.read_text(encoding="utf-8").strip()
    if key:
        os.environ["HOTDATA_API_KEY"] = key
    workspace = option("workspace_id") or os.environ.get("HOTDATA_WORKSPACE", "")
    if workspace:
        os.environ["HOTDATA_WORKSPACE"] = workspace
    url = option("api_url") or os.environ.get("HOTDATA_API_URL", "")
    if url:
        os.environ["HOTDATA_API_URL"] = url


def mode() -> str:
    """Return `mirror` or `standalone`."""
    return "standalone" if option("mode", "mirror") == "standalone" else "mirror"


def memory_roots() -> list[Path]:
    """Return the directories under which a written file counts as a memory file."""
    override = option("memory_root")
    if override:
        return [Path(override).expanduser()]
    return [Path.home() / ".claude" / "projects"]


def is_memory_file(path: Path) -> bool:
    """Return True if `path` is a memory note that this plugin mirrors.

    MEMORY.md is the index that Claude rewrites from the notes, so it is not a note.
    """
    if path.suffix != ".md" or path.name == "MEMORY.md":
        return False
    if "memory" not in path.parts:
        return False
    return any(_under(path, root) for root in memory_roots())


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def label(text: str) -> str:
    """Return `text` as a namespace label: no '.', no '/', never empty."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-")
    return cleaned or "default"


def project_label(cwd: str) -> str:
    """Return the project label for `cwd`, which must not move when the shell does.

    The `project` option wins. Otherwise the repository root names the project, the
    way Claude Code's own memory directory does, so every subdirectory of one
    repository shares one memory. Only outside a repository does the directory name
    decide, and then it is the session's directory, never one the agent cd'd into.
    """
    chosen = option("project") or os.environ.get("HOTDATA_MEMORY_PROJECT", "")
    if chosen:
        return label(chosen)
    start = Path(cwd).expanduser() if cwd else Path.cwd()
    try:
        start = start.resolve()
    except OSError:
        return label(start.name)
    for folder in (start, *start.parents):
        if (folder / ".git").exists():
            return label(folder.name)
    return label(start.name)


def scope_for(cwd: str) -> tuple[str, ...]:
    """Return the namespace for the project at `cwd`."""
    prefix = label(option("scope_prefix", "claude"))
    return (prefix, project_label(cwd))


def budget() -> int:
    """Return the character budget for the session-start block."""
    try:
        return max(200, int(float(option("budget", "2000"))))
    except ValueError:
        return 2000


# A record that is mostly figures is a conclusion, not a method. Three or more of
# these, with no query to reproduce them, is a cached answer rather than a memory.
FIGURE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])")
FIGURE_LIMIT = 3
NEAR = 0.2


def figures(content: str) -> list[str]:
    """Return the figures in `content` that look like results rather than identifiers."""
    found = []
    for match in FIGURE.finditer(content):
        text = match.group(0)
        digits = text.replace(",", "").replace(".", "")
        if len(digits) >= 3 or "." in text:
            found.append(text)
    return found


def reads_as_a_conclusion(content: str) -> bool:
    """Return True if `content` is carrying computed results with nothing to re-run."""
    return len(figures(content)) >= FIGURE_LIMIT


def memory_sql(scope: Sequence[str], alias: str = "mem") -> str:
    """Return the SQL that reads this project's memory from the working database."""
    namespace = "/".join(scope)
    return (
        f"SELECT subject, kind, content, payload, created_at "
        f"FROM {alias}.public.memory_v1 "
        f"WHERE namespace = '{namespace}' AND expired_at IS NULL "
        f"ORDER BY created_at DESC"
    )


def ensure_attached(alias: str = "mem") -> str:
    """Attach the memory database to the working one, so SQL can read both at once.

    Returns the alias on success and an empty string when there is nothing to attach
    or the attach fails. Attaching again is harmless, so this never needs to ask first.
    """
    work = option("work_database")
    store = option("database_id")
    if not work or not store or work == store:
        return ""
    credentials()
    try:
        done = subprocess.run(
            ["hotdata", "databases", "attach", store, "--alias", alias,
             "--database", work],
            capture_output=True, text=True, timeout=120, env=os.environ,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    output = (done.stdout + done.stderr).lower()
    if done.returncode == 0 or "already" in output:
        return alias
    return ""


# ------------------------------------------------------------------------- embedder


def hashed_words(texts: Sequence[str]) -> list[list[float]]:
    """Embed each text as counts of its words, hashed into HASHED_DIMENSIONS.

    It needs no model and no network. It matches words, not meaning.
    """
    vectors = []
    for text in texts:
        vector = [0.0] * HASHED_DIMENSIONS
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            vector[zlib.crc32(word.encode()) % HASHED_DIMENSIONS] += 1.0
        if not any(vector):
            vector[0] = 1.0
        vectors.append(vector)
    return vectors


def embedder() -> tuple[Any, str, int]:
    """Return the embedder named by the `embedder` option, with its model and size."""
    if option("embedder", "hashed") == "openai":
        from hotmemory.openai import OpenAIEmbedder

        made = OpenAIEmbedder()
        if made.dimensions is None:
            raise SystemExit(f"unknown vector size for model {made.model!r}")
        return made, made.model, made.dimensions
    return hashed_words, HASHED_MODEL, HASHED_DIMENSIONS


def cache_path(scope: Sequence[str]) -> Path:
    """Return the file that holds the ready-made session-start block for `scope`."""
    blocks = data_dir() / "blocks"
    blocks.mkdir(parents=True, exist_ok=True)
    return blocks / (f"{'-'.join(scope)}.txt")


def write_cache(scope: Sequence[str], text: str) -> None:
    """Replace the cached block of `scope` in one step."""
    path = cache_path(scope)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


# ---------------------------------------------------------------------------- store


@contextmanager
def locked() -> Iterator[None]:
    """Hold the write lock of this machine. One process writes to a database."""
    path = data_dir() / "write.lock"
    with path.open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def memory(write: bool = False) -> Iterator[Memory]:
    """Open `Memory` over the configured store. A write is serialized and saved."""
    if option("store", "local") == "hotdata":
        from hotmemory.hotdata import HotdataStore

        credentials()
        embed, model, dimensions = embedder()
        database_id = option("database_id")
        settings = {"embedder": embed, "model": model, "dimensions": dimensions}
        if database_id:
            store = HotdataStore.open(database_id, **settings)
        else:
            store = HotdataStore.provision(option("database_name", "claude-memory"), **settings)
        if write:
            with locked():
                yield Memory(store)
        else:
            yield Memory(store)
        return

    path = Path(option("memory_file") or data_dir() / "memory.json")
    if not write:
        yield Memory(MemoryStore(embedder=hashed_words, records=load(path)))
        return
    with locked():
        local = MemoryStore(embedder=hashed_words, records=load(path))
        yield Memory(local)
        save(path, local.records())


def load(path: Path) -> list[Record]:
    """Return the records in the memory file at `path`, or none if it has none."""
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Record(**_parsed(row)) for row in data["records"]]


def save(path: Path, records: Sequence[Record]) -> None:
    """Write `records` to the memory file at `path`, replacing it in one step."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [_row(record) for record in records]
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps({"records": rows}, indent=1) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _row(record: Record) -> dict[str, Any]:
    row = {field.name: getattr(record, field.name) for field in fields(Record) if field.init}
    for name in TIME_FIELDS:
        if row[name] is not None:
            row[name] = row[name].isoformat()
    return row


def _parsed(row: dict[str, Any]) -> dict[str, Any]:
    parsed = dict(row)
    for name in TIME_FIELDS:
        if parsed.get(name) is not None:
            parsed[name] = datetime.fromisoformat(parsed[name])
    return parsed


# -------------------------------------------------------------------------- mapping


def parse_note(text: str) -> dict[str, str]:
    """Return the `name`, `description`, `type`, and `body` of a memory note.

    The frontmatter is read with a small reader, not a YAML parser, because a note
    holds only flat keys and the one nested `metadata.type`. A note without
    frontmatter gives an empty name and the whole text as the body.
    """
    match = FRONTMATTER.match(text)
    if not match:
        return {"name": "", "description": "", "type": DEFAULT_TYPE, "body": text.strip()}
    head, body = match.group(1), match.group(2)
    found: dict[str, str] = {}
    section = ""
    for line in head.splitlines():
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
    kind = found.get("metadata.type", DEFAULT_TYPE)
    return {
        "name": found.get("name", ""),
        "description": found.get("description", ""),
        "type": kind if kind in KIND_OF_TYPE else DEFAULT_TYPE,
        "body": body.strip(),
    }


def note_to_fact(path: Path, text: str, session: str = "") -> Fact | None:
    """Return the fact that the memory note at `path` holds, or None if it is empty."""
    note = parse_note(text)
    if not note["body"]:
        return None
    subject = note["name"] or path.stem
    description = note["description"]
    content = f"{description}\n\n{note['body']}" if description else note["body"]
    sources = [f"file:{path.name}"]
    if session:
        sources.append(f"session:{session}")
    return Fact(
        kind=KIND_OF_TYPE[note["type"]],
        subject=subject,
        content=content,
        cues=tuple(filter(None, [description])),
        tags=(f"type:{note['type']}",),
        sources=tuple(sources),
        payload={"note": path.name, "claude_type": note["type"]},
    )
