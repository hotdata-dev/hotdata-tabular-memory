"""The Hotdata CLI, as a few functions. No SDK, no dependencies.

The `hotdata` binary is the Rust SDK already compiled, already holding the user's
credentials, and already capping and streaming large results. Every call here names
its database explicitly; none depends on the CLI's active database, which is global
state two agents would fight over.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

TIMEOUT = 300
FORMATS = {".csv": "csv", ".tsv": "csv", ".parquet": "parquet",
           ".json": "json", ".jsonl": "json", ".ndjson": "json"}


class HotdataError(RuntimeError):
    """A CLI call that failed, carrying what the CLI said."""


def credentials() -> dict[str, str]:
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
            timeout=timeout, env=credentials(),
        )
    except FileNotFoundError:
        raise HotdataError("the `hotdata` CLI is not on PATH: install it with "
                          "`brew install hotdata-dev/tap/cli` (other ways: "
                          "https://github.com/hotdata-dev/hotdata-cli#install), then "
                          "`hotdata auth login`") from None
    except subprocess.SubprocessError as error:
        raise HotdataError(str(error)) from None
    if done.returncode not in (0, 3):  # 3 is an incomplete preview, not a failure
        message = (done.stderr or done.stdout).strip().splitlines()
        raise HotdataError(_plain(message[0]) if message else f"exit {done.returncode}")
    return done.stdout


def _plain(text: str) -> str:
    """Strip the CLI's colour codes so an error reads as a sentence."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text).strip()


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


def _first(text: str) -> dict:
    found = _objects(text)
    return found[0] if found else {}


def quote(value: str) -> str:
    """Return `value` as a SQL string literal."""
    return "'" + str(value).replace("'", "''") + "'"


# ----------------------------------------------------------------------- queries


HOUSEKEEPING = "/* tabular-memory:internal */ "


def internal(sql: str) -> str:
    """Mark a query the plugin runs for its own purposes, so it never shows up as a
    result worth reusing. The comment survives into the run record's sql_text."""
    return sql if sql.startswith(HOUSEKEEPING) else HOUSEKEEPING + sql


def query(sql: str, database: str) -> dict:
    """Run `sql` against `database` and return the result as the CLI reports it.

    The dict carries `columns`, `rows`, `result_id`, `total_row_count` and `truncated`.
    A streamed result's row chunks are joined.
    """
    text = run(["query", sql, "-d", database, "-o", "json"])
    chunks = _objects(text)
    if not chunks:
        return {"columns": [], "rows": [], "result_id": "", "total_row_count": 0}
    head = dict(chunks[0])
    rows = []
    for chunk in chunks:
        rows.extend(chunk.get("rows") or [])
    head["rows"] = rows
    return head


def rows(sql: str, database: str) -> list[dict]:
    """Run `sql` and return its rows as dicts."""
    result = query(sql, database)
    columns = result.get("columns") or []
    return [dict(zip(columns, row, strict=False)) for row in result.get("rows") or []]


def stream_csv(sql: str, database: str) -> str:
    """Run `sql` and return the whole result as CSV text, streamed by the CLI."""
    return run(["query", sql, "-d", database, "-o", "csv"])


# --------------------------------------------------------------------- databases


def create(name: str, catalog: str) -> dict:
    """Create a database and return `{id, catalog}`."""
    made = _first(run(["databases", "create", "--name", name, "--catalog", catalog,
                       "-o", "json"]))
    if not made.get("id"):
        raise HotdataError("create returned no database id")
    return {"id": made["id"], "catalog": made.get("default_catalog") or catalog}


def catalog_of(database: str) -> str:
    """Return the SQL alias of `database`."""
    shown = _first(run(["databases", "show", database, "-o", "json"]))
    return str(shown.get("default_catalog") or "default")


def fork(database: str, name: str, expires: str = "") -> dict:
    """Fork `database` and return `{id, snapshot_id}`.

    The fork's provenance is the only place a snapshot id can be read today.
    """
    args = ["databases", "fork", database, "--name", name, "-o", "json"]
    if expires:
        args += ["--expires-at", expires]
    made = _first(run(args))
    origin = made.get("forked_from") or {}
    return {"id": made.get("id", ""), "snapshot_id": origin.get("snapshot_id"),
            "catalog": made.get("default_catalog", "")}


def attach(database: str, other: str, alias: str) -> None:
    """Make `other`'s tables visible inside `database` as `<alias>.public.<table>`."""
    run(["databases", "attach", other, "--alias", alias, "--database", database])


def workspaces() -> list[dict]:
    text = run(["workspaces", "list", "-o", "json"])
    found = _objects(text)
    return found[0] if found and isinstance(found[0], list) else found


# ------------------------------------------------------------------------ tables


def load_file(database: str, table: str, source: str, mode: str = "replace") -> None:
    """Load a file into `table`, creating the table if it is missing.

    A URL is handed to the server to fetch, so the bytes never pass through this
    machine; a path is uploaded.
    """
    remote = source.startswith(("http://", "https://"))
    name = source.split("?", 1)[0] if remote else source
    fmt = FORMATS.get(Path(name).suffix.lower())
    args = ["databases", "tables", "load", table, "--database", database,
            "--url" if remote else "--file", source, "--mode", mode]
    if fmt:
        args += ["--format", fmt]
    run(args, timeout=900)


def load_result(database: str, table: str, result_id: str) -> None:
    """Materialise a stored query result as `table`. The rows never leave the server."""
    run(["databases", "tables", "load", table, "--database", database,
         "--result-id", result_id, "--mode", "replace"], timeout=900)


def tables(database: str, catalog: str) -> list[dict]:
    """Return `[{name, rows}]` for the database's own tables."""
    names = rows(internal(
        f"SELECT table_name FROM information_schema.tables "
        f"WHERE table_catalog = {quote(catalog)} AND table_schema = 'public' "
        f"ORDER BY table_name"), database)
    out = []
    for row in names:
        name = row["table_name"]
        try:
            count = rows(internal(f"SELECT count(*) AS n FROM {catalog}.public.{name}"), database)
            out.append({"name": name, "rows": int(count[0]["n"]) if count else 0})
        except HotdataError:
            out.append({"name": name, "rows": None})
    return out


def columns(database: str, catalog: str, table: str) -> list[dict]:
    """Return `[{name, type}]` for `table`, in declared order."""
    found = rows(internal(
        f"SELECT column_name, data_type FROM information_schema.columns "
        f"WHERE table_catalog = {quote(catalog)} AND table_name = {quote(table)} "
        f"ORDER BY ordinal_position"), database)
    return [{"name": r["column_name"], "type": r["data_type"]} for r in found]


def sample(database: str, catalog: str, table: str, n: int = 3) -> list[dict]:
    return rows(internal(f"SELECT * FROM {catalog}.public.{table} LIMIT {int(n)}"), database)


def remove_table(database: str, table: str) -> None:
    run(["databases", "tables", "remove", table, "--database", database])


def table_names(database: str, catalog: str) -> list[str]:
    """Return the database's own table names, one cheap query."""
    found = rows(internal(
        f"SELECT table_name FROM information_schema.tables "
        f"WHERE table_catalog = {quote(catalog)} AND table_schema = 'public' "
        f"ORDER BY table_name"), database)
    return [r["table_name"] for r in found]


def ranges(database: str, catalog: str, table: str, cols: list[dict]) -> dict[str, tuple]:
    """Return `{column: (min, max)}` for the table's timestamp and date columns."""
    timed = [c["name"] for c in cols if re.search(r"timestamp|date", c["type"], re.I)]
    if not timed:
        return {}
    parts = ", ".join(f'min("{c}") AS "lo_{i}", max("{c}") AS "hi_{i}"' for i, c in enumerate(timed))
    found = rows(internal(f"SELECT {parts} FROM {catalog}.public.{table}"), database)
    if not found:
        return {}
    row = found[0]
    return {c: (row.get(f"lo_{i}"), row.get(f"hi_{i}")) for i, c in enumerate(timed)}


def query_runs(database: str, limit: int = 40) -> list[dict]:
    """Return recent query runs, newest first, as the CLI reports them.

    Each carries `sql_text`, `sql_hash`, `result_id`, `row_count`, `snapshot_id`,
    `status` and `error_message`.
    """
    text = run(["databases", "queries", "list", "-d", database, "--limit", str(limit),
                "-o", "json"])
    found = _objects(text)
    if not found:
        return []
    head = found[0]
    if isinstance(head, list):
        return head
    for key in ("runs", "queries", "items", "data"):
        if isinstance(head.get(key), list):
            return head[key]
    return []
