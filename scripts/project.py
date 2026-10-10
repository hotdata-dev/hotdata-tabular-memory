"""Which project this is, which database it owns, and where the plugin keeps state.

One database per project, discovered the same way by every agent working in it, so a
swarm shares a database without anyone passing an id around. Nothing here relies on
the CLI's active database, which is global state two agents would fight over.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import hotdata

DATA_EXTENSIONS = {".csv", ".tsv", ".parquet", ".jsonl", ".json", ".ndjson"}


def option(name: str, default: str = "") -> str:
    """Return a plugin option, which Claude Code exports as CLAUDE_PLUGIN_OPTION_<KEY>."""
    value = os.environ.get(f"CLAUDE_PLUGIN_OPTION_{name.upper()}", "").strip()
    return value or default


def data_dir() -> Path:
    root = os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.expanduser("~/.claude/hotdata")
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def label(text: str) -> str:
    """Return `text` as a label safe for a catalog alias: lowercase, [a-z0-9_]."""
    cleaned = re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_")
    if not cleaned or not cleaned[0].isalpha():
        cleaned = "p_" + cleaned
    return cleaned[:48]


def project_label(cwd: str = "") -> str:
    """Return the project label for `cwd`, which must not move when the shell does.

    The `project` option wins. Otherwise the repository root names the project, the
    way Claude Code's own memory directory is named. Only outside a repository does
    a directory name decide.
    """
    chosen = option("project") or os.environ.get("HOTDATA_PROJECT", "")
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


# ---------------------------------------------------------------------- registry


def registry_path() -> Path:
    return data_dir() / "projects.json"


def _registry() -> dict:
    path = registry_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_registry(entries: dict) -> None:
    path = registry_path()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(entries, indent=1), encoding="utf-8")
    os.replace(temporary, path)


def record(project: str, database: str, catalog: str) -> None:
    entries = _registry()
    entries[project] = {"database": database, "catalog": catalog,
                        "created_at": time.strftime("%FT%TZ", time.gmtime()),
                        "forks": entries.get(project, {}).get("forks", [])}
    _save_registry(entries)


def record_fork(project: str, fork: dict) -> None:
    entries = _registry()
    entry = entries.setdefault(project, {"database": "", "catalog": "", "forks": []})
    entry.setdefault("forks", []).append(fork)
    _save_registry(entries)


def project_database(cwd: str = "", create: bool = False) -> dict | None:
    """Return `{database, catalog, project}` for this project, or None.

    The `database` option pins it. Otherwise the registry answers. With `create`, a
    project with no database gets one, named after the project.
    """
    project = project_label(cwd)
    pinned = option("database")
    if pinned:
        catalog = option("catalog") or hotdata.catalog_of(pinned)
        return {"database": pinned, "catalog": catalog, "project": project}
    entry = _registry().get(project)
    if entry and entry.get("database"):
        return {"database": entry["database"], "catalog": entry["catalog"],
                "project": project}
    if not create:
        return None
    made = hotdata.create(name=project, catalog=project)
    record(project, made["id"], made["catalog"])
    return {"database": made["id"], "catalog": made["catalog"], "project": project}


# -------------------------------------------------------------------- workspace


def workspace_id() -> str:
    """Return the workspace id the API needs: the option, the CLI's config, or the CLI."""
    chosen = option("workspace") or os.environ.get("HOTDATA_WORKSPACE", "")
    if chosen:
        return chosen
    cached = data_dir() / "workspace.txt"
    if cached.exists():
        return cached.read_text(encoding="utf-8").strip()
    found = ""
    config = Path.home() / ".hotdata" / "config.yml"
    if config.exists():
        text = config.read_text(encoding="utf-8")
        match = re.search(r"public_id:\s*(\S+)", text)
        if match:
            found = match.group(1)
    if not found:
        rows = hotdata.workspaces()
        found = rows[0]["public_id"] if rows else ""
    if found:
        cached.write_text(found, encoding="utf-8")
    return found


# ------------------------------------------------------------------------ caches


def cache_path(kind: str, key: str) -> Path:
    folder = data_dir() / kind
    folder.mkdir(parents=True, exist_ok=True)
    return folder / (re.sub(r"[^A-Za-z0-9_-]+", "-", key) + ".txt")


def write_cache(kind: str, key: str, text: str) -> None:
    path = cache_path(kind, key)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def fit(lines: list[str], budget: int) -> list[str]:
    """Return the lines that fit, stopping before the first that would not."""
    kept, used = [], 0
    for line in lines:
        used += len(line) + 1
        if used > budget:
            break
        kept.append(line)
    return kept
