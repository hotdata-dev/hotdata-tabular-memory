"""Definitions: named filters and derived sets that every agent gets identically.

A definition is a saved query on the server. In an agent's SQL, `{{name}}` expands to
that query as a subquery, so an agent writing `FROM {{clean_set}}` cannot invent a
different filter. The engine has no DDL, so this is a view without `CREATE VIEW`.
"""

from __future__ import annotations

import json
import re

import api
import hotdata
import project

NAME = re.compile(r"^[a-z][a-z0-9_]*$")
SEP = "__"


def stored_name(project_label: str, name: str) -> str:
    """A definition is saved as `<project>__<name>`, so projects do not see each other's."""
    return f"{project_label}{SEP}{name}"
REF = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
AFTER_FROM = re.compile(r"(\b(?:from|join)\s+)\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}",
                        re.IGNORECASE)


def check_name(name: str) -> str:
    if not NAME.match(name):
        raise hotdata.HotdataError(
            f"a definition name is lowercase letters, digits and underscores, "
            f"starting with a letter: {name!r} is not")
    return name


def define(project_label: str, name: str, sql: str, description: str = "") -> dict:
    """Create the definition, or add a version if one by that name exists."""
    check_name(name)
    sql = sql.strip().rstrip(";")
    if not re.match(r"^\s*(select|with)\b", sql, re.I):
        # The first agent to define one wrote the WHERE clause alone. A definition has
        # to stand in a FROM, so it is a whole SELECT, and the refusal shows the shape.
        raise hotdata.HotdataError(
            f"a definition is a whole SELECT, so that {{{{{name}}}}} can stand in a FROM. "
            f"Yours starts with {sql[:40]!r}. Write it as:\n"
            f'  hd define {name} "SELECT * FROM <catalog>.public.<table> WHERE {sql[:60]}..."')
    full = stored_name(project_label, name)
    existing = api.find(full)
    if existing:
        made = api.update(existing["id"], sql, description or None)
    else:
        made = api.create(full, sql, description)
    _forget_cache()
    return {"name": name, "id": made.get("id"), "version": made.get("latest_version"),
            "sql_hash": made.get("sql_hash", ""), "new": not existing}


def all_definitions(project_label: str, refresh: bool = False) -> dict[str, dict]:
    """Return this project's `{name: {id, sql, description, version, sql_hash}}`, cached."""
    path = project.cache_path("defs", project_label)
    if not refresh and path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    prefix = stored_name(project_label, "")
    found: dict[str, dict] = {}
    for summary in api.list_queries():
        full = summary.get("name", "")
        if not full.startswith(prefix):
            continue  # another project's, or not a definition at all
        name = full[len(prefix):]
        if not NAME.match(name):
            continue
        detail = api.get(summary["id"])
        found[name] = {"id": summary["id"], "sql": detail.get("sql", ""),
                       "description": detail.get("description", ""),
                       "version": detail.get("latest_version"),
                       "sql_hash": detail.get("sql_hash", "")}
    project.write_cache("defs", project_label, json.dumps(found))
    return found


def _forget_cache() -> None:
    folder = project.data_dir() / "defs"
    if folder.exists():
        for path in folder.glob("*.txt"):
            path.unlink()


def expand(sql: str, definitions: dict[str, dict] | None = None,
           project_label: str = "") -> str:
    """Replace each `{{name}}` with its definition as a subquery.

    After FROM or JOIN the subquery is aliased to the name, so columns resolve as the
    agent expects. Elsewhere it is a bare parenthesised query. An unknown name is
    refused, naming the ones that exist.
    """
    if "{{" not in sql:
        return sql
    known = definitions if definitions is not None else all_definitions(project_label)

    def body(name: str) -> str:
        if name not in known:
            names = ", ".join(sorted(known)) or "none yet — `hd define <name> \"<sql>\"`"
            raise hotdata.HotdataError(f"no definition named {name!r}. Known: {names}")
        inner = known[name]["sql"].strip().rstrip(";")
        return f"({inner})"

    sql = AFTER_FROM.sub(lambda m: f"{m.group(1)}{body(m.group(2))} AS {m.group(2)}", sql)
    return REF.sub(lambda m: body(m.group(1)), sql)
