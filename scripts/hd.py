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


def print_csv(columns: list, rows: list) -> None:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(columns)
    writer.writerows(rows)
    sys.stdout.write(out.getvalue())


# ---------------------------------------------------------------------- commands


def cmd_load(args) -> int:
    path = Path(args.file).expanduser().resolve()
    if not path.exists():
        raise HotdataError(f"no such file: {path}")
    table = args.table or project.label(path.stem)
    e = entry(create=True)
    started = time.time()
    hotdata.load_file(e["database"], table, str(path), "replace" if args.replace else "append"
                      if args.append else "replace")
    took = time.time() - started
    print(f"loaded {path.name} -> {e['catalog']}.public.{table} in {took:.1f}s\n")
    print(orient.refresh(e, orient.budget()))
    return 0


def cmd_query(args) -> int:
    e = entry()
    sql = defs.expand(args.sql, project_label=e["project"])
    cap = row_cap()
    if not args.all and discipline.classify(sql) == "unbounded":
        print(discipline.refusal(sql, cap), file=sys.stderr)
        return 2
    if args.all:
        sys.stdout.write(hotdata.stream_csv(sql, e["database"]))
        return 0
    result = hotdata.query(sql, e["database"])
    total = int(result.get("total_row_count") or len(result.get("rows") or []))
    rows, footer = discipline.trim(result.get("rows") or [], total, cap)
    print_csv(result.get("columns") or [], rows)
    if footer:
        print(footer)
    return 0


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
    sql = defs.expand(args.sql, project_label=e["project"])
    result = hotdata.query(sql, e["database"])
    rid = result.get("result_id")
    if not rid:
        raise HotdataError("the query returned no result id to materialise")
    hotdata.load_result(e["database"], args.table, rid)
    total = result.get("total_row_count")
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

    s = sub.add_parser("load", help="load a file into the project database")
    s.add_argument("file"); s.add_argument("--table")
    s.add_argument("--append", action="store_true"); s.add_argument("--replace", action="store_true")
    s.set_defaults(run=cmd_load)

    s = sub.add_parser("query", help="run SQL; {{name}} expands to a definition")
    s.add_argument("sql"); s.add_argument("--all", action="store_true",
                                          help="stream every row as CSV, uncapped")
    s.set_defaults(run=cmd_query)

    s = sub.add_parser("define", help="name a filter or derived set for every agent")
    s.add_argument("name"); s.add_argument("sql"); s.add_argument("--description", default="")
    s.set_defaults(run=cmd_define)

    s = sub.add_parser("defs", help="list definitions"); s.set_defaults(run=cmd_defs)

    s = sub.add_parser("share", help="materialise a query result as a table")
    s.add_argument("table"); s.add_argument("sql"); s.set_defaults(run=cmd_share)

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
