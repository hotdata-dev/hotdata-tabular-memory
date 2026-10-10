"""hd: the Hotdata command for agents. One database per project; nothing into context.

    hd load <file>            the file becomes a table; prints the orientation
    hd query "<sql>"          answer a question; {{name}} expands to a definition
    hd define <name> "<sql>"  a filter or derived set every agent shares
    hd share <table> "<sql>"  keep a result on the server as a table
    hd fork / attach / defs / status / orient
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys
import time
from pathlib import Path

import defs
import discipline
import hotdata
import orient
import project
from hotdata import HotdataError


def entry(create: bool = False) -> dict:
    found = project.project_database(os.getcwd(), create=create)
    if not found:
        raise HotdataError("this project has no database yet: `hd load <file>` makes one")
    return found


def row_cap() -> int:
    try:
        return max(1, int(project.option("row_cap", "200")))
    except ValueError:
        return 200


NOT_FOUND = re.compile(r"not found|does not exist|no such|unknown (table|column)|cannot resolve", re.I)


def with_names(error: HotdataError, e: dict) -> HotdataError:
    """Append what exists to an error about something that does not, to save the retry."""
    if not NOT_FOUND.search(str(error)):
        return error
    try:
        tables = ", ".join(f"{e['catalog']}.public.{n}" for n in hotdata.table_names(e["database"], e["catalog"]))
        names = ", ".join(f"{{{{{n}}}}}" for n in sorted(defs.all_definitions(e["project"]))) or "none"
    except HotdataError:
        return error
    return HotdataError(f"{error}\n  tables here: {tables}\n  definitions: {names}")


def run_one(e: dict, sql: str, cap: int, stream_all: bool) -> tuple[int, str]:
    """Expand, check, run one query; return (exit code, text to print)."""
    sql = defs.expand(sql, project_label=e["project"])
    if not stream_all and discipline.classify(sql) == "unbounded":
        return 2, discipline.refusal(sql, cap)
    try:
        if stream_all:
            return 0, hotdata.stream_csv(sql, e["database"])
        result = hotdata.query(sql, e["database"])
    except HotdataError as error:
        raise with_names(error, e) from None
    total = int(result.get("total_row_count") or len(result.get("rows") or []))
    rows, footer = discipline.trim(result.get("rows") or [], total, cap)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(result.get("columns") or [])
    writer.writerows(rows)
    return 0, out.getvalue() + (footer + "\n" if footer else "")


def print_csv(columns: list, rows: list) -> None:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(columns)
    writer.writerows(rows)
    sys.stdout.write(out.getvalue())


# ---------------------------------------------------------------------- commands


def cmd_load(args) -> int:
    remote = args.file.startswith(("http://", "https://"))
    if remote:
        source, shown = args.file, args.file.split("?", 1)[0].rsplit("/", 1)[-1]
        stem = Path(shown).stem
    else:
        path = Path(args.file).expanduser().resolve()
        if not path.exists():
            raise HotdataError(f"no such file: {path}")
        source, shown, stem = str(path), path.name, path.stem
    table = args.table or project.label(stem)
    e = entry(create=True)
    started = time.time()
    hotdata.load_file(e["database"], table, source, "append" if args.append else "replace")
    took = time.time() - started
    how = "fetched by the server" if remote else "uploaded"
    print(f"loaded {shown} -> {e['catalog']}.public.{table} in {took:.1f}s ({how})\n")
    print(orient.refresh(e, orient.budget()))
    return 0


def cmd_query(args) -> int:
    e = entry()
    code, text = run_one(e, args.sql, row_cap(), args.all)
    print(text, end="", file=sys.stderr if code else sys.stdout)
    return code


# --- 1: several queries, one call, one turn
LABEL = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+)$", re.S)
NAME_COMMENT = re.compile(r"^\s*--\s*([A-Za-z_][A-Za-z0-9_]*)\s*$")


def split_batch(args_sql: list[str], stdin_text: str) -> list[tuple[str, str]]:
    """Return `[(label, sql)]` from positional `label: sql` args or a stdin script.

    In a script, statements end at a `;` at the end of a line, and a `-- name` comment
    line labels the statement that follows it. Unlabelled statements are numbered.
    """
    items: list[tuple[str, str]] = []
    for i, raw in enumerate(args_sql, 1):
        m = LABEL.match(raw)
        items.append((m.group(1), m.group(2)) if m else (f"q{i}", raw))
    if stdin_text.strip():
        label, buf = "", []
        for line in stdin_text.splitlines():
            m = NAME_COMMENT.match(line)
            if m:
                label = m.group(1)
                continue
            buf.append(line)
            if line.rstrip().endswith(";"):
                sql = "\n".join(buf).strip().rstrip(";").strip()
                if sql:
                    items.append((label or f"q{len(items) + 1}", sql))
                label, buf = "", []
        tail = "\n".join(buf).strip().rstrip(";").strip()
        if tail:
            items.append((label or f"q{len(items) + 1}", tail))
    return items


def cmd_batch(args) -> int:
    """Run every query and print every result, so several questions cost one turn.

    A failure in one query does not stop the others: each result or error is printed
    under its label, and the exit code is the worst of them.
    """
    e = entry()
    # Read a script from stdin only when no queries were given as arguments. An agent's
    # shell keeps stdin open, so reading it unasked blocks forever waiting for EOF.
    stdin_text = "" if args.sql or sys.stdin.isatty() else sys.stdin.read()
    items = split_batch(args.sql, stdin_text)
    if not items:
        raise HotdataError("nothing to run: pass 'label: SELECT ...' arguments or a script on stdin")
    cap = row_cap()
    answered = 0
    for label, sql in items:
        print(f"== {label} ==")
        try:
            code, text = run_one(e, sql, cap, False)
        except HotdataError as error:
            code, text = 1, f"error: {error}\n"
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
        answered += code == 0
    # One refused query must not throw away the answers beside it. A non-zero exit
    # makes the whole call read as a failure, and an agent then discards results it
    # already has; each query's own outcome is printed under its label instead.
    if answered:
        return 0
    print(f"every query failed: {len(items)} of {len(items)}", file=sys.stderr)
    return 2


def cmd_define(args) -> int:
    e = project.project_database(os.getcwd(), create=True)
    made = defs.define(e["project"], args.name, args.sql, args.description)
    if e:
        orient.refresh(e, orient.budget())
    verb = "defined" if made["new"] else f"updated to v{made['version']}"
    print(f"{{{{{args.name}}}}} {verb}  (sql_hash {made['sql_hash'][:12]})")
    return 0


def cmd_defs(args) -> int:
    found = defs.all_definitions(entry()["project"], refresh=True)
    if not found:
        print("no definitions yet: hd define <name> \"<sql>\" --description \"...\"")
        return 0
    for name, d in sorted(found.items()):
        print(f"{{{{{name}}}}} v{d['version']}  {d.get('description') or ''}")
        print(f"    {d['sql'][:140]}")
    return 0


def cmd_share(args) -> int:
    e = entry()
    if args.result_id:
        rid, total = args.result_id, None
    else:
        if not args.sql:
            raise HotdataError("give a query, or --result-id from the orientation's recent results")
        sql = defs.expand(args.sql, project_label=e["project"])
        try:
            result = hotdata.query(sql, e["database"])
        except HotdataError as error:
            raise with_names(error, e) from None
        rid = result.get("result_id")
        if not rid:
            raise HotdataError("the query returned no result id to materialise")
        total = result.get("total_row_count")
    hotdata.load_result(e["database"], args.table, rid)
    if total is None:
        total = hotdata.rows(hotdata.internal(
            f"SELECT count(*) AS n FROM {e['catalog']}.public.{args.table}"), e["database"])[0]["n"]
    print(f"{e['catalog']}.public.{args.table}: {total:,} rows, on the server. "
          f"Query it like any table.")
    orient.refresh(e, orient.budget())
    return 0


def cmd_fork(args) -> int:
    e = entry()
    name = args.name or f"{e['project']}-fork-{time.strftime('%H%M%S')}"
    made = hotdata.fork(e["database"], name, args.expires)
    project.record_fork(e["project"], {"id": made["id"], "name": name,
                                       "snapshot_id": made["snapshot_id"]})
    print(f"forked {e['database']} at snapshot {made['snapshot_id']} -> {made['id']}")
    print("Query the fork with -d, or set CLAUDE_PLUGIN_OPTION_DATABASE to work in it.")
    return 0


def cmd_attach(args) -> int:
    e = entry()
    alias = args.alias or project.label(args.database_id[-8:])
    hotdata.attach(e["database"], args.database_id, alias)
    print(f"attached {args.database_id} as {alias}: its tables are {alias}.public.<table>")
    orient.refresh(e, orient.budget())
    return 0


def cmd_status(args) -> int:
    e = project.project_database(os.getcwd())
    print(f"project:   {project.project_label(os.getcwd())}")
    if not e:
        print("database:  none yet — `hd load <file>` creates one")
        return 0
    print(f"database:  {e['database']}  (catalog {e['catalog']})")
    print(f"guard:     {project.option('read_guard', 'on')}, "
          f"{project.option('guard_threshold_mb', '8')}MB")
    print(f"row cap:   {row_cap()}")
    for t in hotdata.tables(e["database"], e["catalog"]):
        print(f"  table    {t['name']}  {t['rows']:,} rows" if t["rows"] is not None
              else f"  table    {t['name']}")
    try:
        for name in sorted(defs.all_definitions(e["project"])):
            print(f"  def      {{{{{name}}}}}")
    except HotdataError as error:
        print(f"  defs     unavailable: {error}")
    return 0


def cmd_orient(args) -> int:
    e = entry()
    text = (orient.refresh(e, orient.budget()) if args.refresh
            else orient.cached(e) or orient.refresh(e, orient.budget()))
    print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="hd", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("load", help="load a file or URL into the project database")
    s.add_argument("file", help="a local path, or an http(s) URL the server fetches")
    s.add_argument("--table")
    s.add_argument("--append", action="store_true"); s.add_argument("--replace", action="store_true")
    s.set_defaults(run=cmd_load)

    s = sub.add_parser("query", help="run SQL; {{name}} expands to a definition")
    s.add_argument("sql"); s.add_argument("--all", action="store_true",
                                          help="stream every row as CSV, uncapped")
    s.set_defaults(run=cmd_query)

    s = sub.add_parser("batch", help="run several queries in one call: 'label: SELECT ...' args, or a script on stdin")
    s.add_argument("sql", nargs="*"); s.set_defaults(run=cmd_batch)

    s = sub.add_parser("define", help="name a filter or derived set for every agent")
    s.add_argument("name"); s.add_argument("sql"); s.add_argument("--description", default="")
    s.set_defaults(run=cmd_define)

    s = sub.add_parser("defs", help="list definitions"); s.set_defaults(run=cmd_defs)

    s = sub.add_parser("share", help="materialise a query result as a table")
    s.add_argument("table"); s.add_argument("sql", nargs="?")
    s.add_argument("--result-id", help="reuse a stored result instead of running a query")
    s.set_defaults(run=cmd_share)

    s = sub.add_parser("fork", help="an isolated copy of the project database")
    s.add_argument("--name"); s.add_argument("--expires", default="24h")
    s.set_defaults(run=cmd_fork)

    s = sub.add_parser("attach", help="see another database's tables from this one")
    s.add_argument("database_id"); s.add_argument("--alias"); s.set_defaults(run=cmd_attach)

    s = sub.add_parser("status", help="project, database, tables, definitions")
    s.set_defaults(run=cmd_status)

    s = sub.add_parser("orient", help="print what an agent is told at session start")
    s.add_argument("--refresh", action="store_true"); s.set_defaults(run=cmd_orient)

    args = p.parse_args(argv)
    try:
        return args.run(args)
    except HotdataError as error:
        print(f"hd: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
