"""Configuration, scope, and the cached session block. No dependencies."""

from __future__ import annotations

import os
import re
from pathlib import Path

import hotdata
from store import FileStore, Store

DEFAULT_BUDGET = 2000


def option(name: str, default: str = "") -> str:
    """Return a plugin option, which Claude Code exports as CLAUDE_PLUGIN_OPTION_<KEY>."""
    value = os.environ.get(f"CLAUDE_PLUGIN_OPTION_{name.upper()}", "").strip()
    return value or default


def data_dir() -> Path:
    root = os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.expanduser(
        "~/.claude/hotdata-memory")
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def label(text: str) -> str:
    """Return `text` as a namespace label: no '/', never empty."""
    return re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-") or "default"


def project_label(cwd: str) -> str:
    """Return the project label for `cwd`, which must not move when the shell does.

    The `project` option wins. Otherwise the repository root names the project, the
    way Claude Code's own memory directory is named. Only outside a repository does
    a directory name decide.
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


def namespace_for(cwd: str) -> str:
    """Return the namespace this project's records live under."""
    return f"{label(option('scope_prefix', 'claude'))}/{project_label(cwd)}"


def budget() -> int:
    try:
        return max(200, int(float(option("budget", str(DEFAULT_BUDGET)))))
    except ValueError:
        return DEFAULT_BUDGET


def catalog_for(database: str) -> str:
    """Return the database's SQL alias, asking the CLI once and remembering it."""
    chosen = option("catalog")
    if chosen:
        return chosen
    cached = data_dir() / "catalogs" / f"{label(database)}.txt"
    if cached.exists():
        return cached.read_text(encoding="utf-8").strip()
    alias = hotdata.catalog_of(database)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(alias, encoding="utf-8")
    return alias


def store() -> Store:
    """Return the store this project writes to.

    Memory goes in the database the project already queries, so one statement can
    read memory beside the data. With no database configured it falls back to a file.
    """
    database = option("database") or option("work_database")
    if not database:
        return FileStore(Path(option("memory_file") or data_dir() / "memory.json"))
    return Store(database, catalog_for(database))


def memory_sql(namespace: str, catalog: str = "") -> str:
    """Return the SQL an agent runs to read this project's memory beside its data."""
    name = f"{catalog or 'default'}.public.{__import__('store').TABLE}"
    where = f"namespace = {hotdata.quote(namespace)}"
    return (f"SELECT subject, kind, content, query FROM {name} "
            f"WHERE {where} AND id NOT IN "
            f"(SELECT supersedes FROM {name} WHERE {where} AND supersedes <> '') "
            f"ORDER BY created_at DESC")


# ------------------------------------------------------------------ session block


def cache_path(namespace: str) -> Path:
    blocks = data_dir() / "blocks"
    blocks.mkdir(parents=True, exist_ok=True)
    return blocks / (label(namespace) + ".txt")


def write_cache(namespace: str, text: str) -> None:
    path = cache_path(namespace)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def fit(lines: list[str], budget_chars: int) -> list[str]:
    """Return the lines that fit, stopping before the first that would not."""
    kept, used = [], 0
    for line in lines:
        used += len(line) + 1
        if used > budget_chars:
            break
        kept.append(line)
    return kept
