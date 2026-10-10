"""The Hotdata CLI, as a few functions. No SDK, no dependencies.

The `hotdata` binary is the Rust SDK already compiled, already holding the user's
credentials, and already capping and streaming large results. A plugin that shells
out to it needs no interpreter environment of its own.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

TIMEOUT = 120


class HotdataError(RuntimeError):
    """A CLI call that failed, carrying what the CLI said."""


def _credentials() -> dict[str, str]:
    """Return the environment a call needs, filling the key in from disk if missing.

    A hook does not inherit a login shell, so the key comes from the environment
    first and then from the file `hotdata auth login` writes.
    """
    env = dict(os.environ)
    if not env.get("HOTDATA_API_KEY"):
        stored = Path.home() / ".hotdata" / "api-key"
        if stored.exists():
            env["HOTDATA_API_KEY"] = stored.read_text(encoding="utf-8").strip()
    return env


def run(args: Sequence[str], timeout: int = TIMEOUT) -> str:
    """Run `hotdata` with `args` and return its stdout, or raise HotdataError."""
    try:
        done = subprocess.run(
            ["hotdata", *args], capture_output=True, text=True,
            timeout=timeout, env=_credentials(),
        )
    except FileNotFoundError:
        raise HotdataError("the `hotdata` CLI is not on PATH") from None
    except subprocess.SubprocessError as error:
        raise HotdataError(str(error)) from None
    if done.returncode not in (0, 3):  # 3 is an incomplete preview, not a failure
        message = (done.stderr or done.stdout).strip().splitlines()
        raise HotdataError(message[0] if message else f"exit {done.returncode}")
    return done.stdout


def _objects(text: str) -> list[dict]:
    """Return every JSON object in `text`. A streamed result arrives as several."""
    decoder = json.JSONDecoder()
    found, index = [], 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        value, index = decoder.raw_decode(text, index)
        found.append(value)
    return found


def query(sql: str, database: str) -> list[dict]:
    """Run `sql` against `database` and return its rows as dicts."""
    text = run(["query", sql, "-d", database, "-o", "json"])
    rows: list[dict] = []
    for chunk in _objects(text):
        columns = chunk.get("columns") or []
        for row in chunk.get("rows") or []:
            rows.append(dict(zip(columns, row, strict=False)))
    return rows


def append(rows: Sequence[dict], table: str, database: str,
           mode: str = "append", key: str = "") -> None:
    """Load `rows` into `table`, creating it if it is missing.

    A `delete`, `update` or `upsert` load matches existing rows by a key, which the
    table does not declare, so the caller names one.
    """
    if not rows:
        return
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8")
    try:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
        handle.close()
        # `databases load` resolves the target from the CLI's active database, which
        # is global state two agents would fight over. `tables load` takes the id.
        args = ["databases", "tables", "load", table, "--database", database,
                "--file", handle.name, "--format", "json", "--mode", mode]
        if key:
            args += ["--key", key]
        run(args)
    finally:
        os.unlink(handle.name)


def table_exists(catalog: str, table: str, database: str) -> bool:
    """Return True if `table` is already in `catalog`."""
    sql = (f"SELECT 1 FROM information_schema.tables "
           f"WHERE table_catalog = '{catalog}' AND table_name = '{table}' LIMIT 1")
    try:
        return bool(query(sql, database))
    except HotdataError:
        return False


def quote(value: str) -> str:
    """Return `value` as a SQL string literal."""
    return "'" + str(value).replace("'", "''") + "'"


def catalog_of(database: str) -> str:
    """Return the SQL alias of `database`, so nobody has to configure it."""
    text = run(["databases", "show", database, "-o", "json"])
    for chunk in _objects(text):
        alias = chunk.get("default_catalog")
        if alias:
            return str(alias)
    return "default"
