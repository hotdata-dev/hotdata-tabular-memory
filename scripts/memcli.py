"""The memory commands an agent runs: recall, list, remember, supersede, forget.

Everything comes from the plugin's configuration, so no store flags are needed.
"""

from __future__ import annotations

import argparse
import os
import sys

import bridge
from hotdata import HotdataError
from store import Record, figures, normalize, reads_as_a_conclusion


def namespace_of(args) -> str:
    if args.scope:
        return args.scope
    return bridge.namespace_for(os.getcwd())


def cmd_recall(args) -> int:
    store = bridge.store()
    found = store.search(namespace_of(args), args.query, limit=args.limit)
    if not found:
        held = len(store.current(namespace_of(args), limit=500))
        print(f"no record matches those words. {held} record(s) are stored; "
              f"`memory list` prints them." if held else "no memory is stored yet.")
        return 0
    print("\n".join(bridge.fit([r.line() for r in found], args.budget)))
    return 0


def cmd_list(args) -> int:
    namespace = namespace_of(args)
    found = bridge.store().current(namespace, limit=args.limit)
    if not found:
        print(f"no memory under {namespace}.")
        return 0
    lines = [f"{r.id}  {r.line()}" for r in found]
    print("\n".join(bridge.fit(lines, args.budget)))
    return 0


def cmd_remember(args) -> int:
    """Write one record, after the two checks that keep the store worth reading."""
    namespace = namespace_of(args)
    if not args.query and reads_as_a_conclusion(args.content):
        found = ", ".join(figures(args.content)[:5])
        print(f"refused: this records figures ({found}) with no query to reproduce "
              f"them.\nA figure was true of one query on one day. Record the query "
              f'that returns it with --query "<sql>", or record the method instead.',
              file=sys.stderr)
        return 2
    store = bridge.store()
    if args.subject and not args.force:
        clash = next((r for r in store.by_subject(namespace, args.subject)
                      if normalize(r.content) != normalize(args.content)), None)
        if clash is not None:
            print(f"refused: memory already holds a record on {args.subject} that says "
                  f"something different.\n\n  stored: {clash.id}\n  "
                  f"{clash.content[:300]}\n\nDecide which is right rather than storing "
                  f'both:\n  memory supersede {clash.id} "<the correct fact>"\n'
                  f"or use a different --subject, or --force.", file=sys.stderr)
            return 2
    record = Record(namespace=namespace, subject=args.subject, kind=args.kind,
                    content=args.content, query=args.query,
                    tags=",".join(args.tags), sources=",".join(args.sources),
                    actor=args.actor)
    print("\n".join(store.write([record])))
    return 0


def cmd_supersede(args) -> int:
    """Append a record replacing `id`. The old one stays readable in history."""
    namespace = namespace_of(args)
    store = bridge.store()
    old = next((r for r in store.current(namespace, limit=500) if r.id == args.id), None)
    if old is None:
        print(f"no current record with id {args.id}", file=sys.stderr)
        return 2
    record = Record(namespace=namespace, subject=args.subject or old.subject,
                    kind=args.kind or old.kind, content=args.content,
                    query=args.query, supersedes=args.id, actor=args.actor)
    print("\n".join(store.write([record])))
    return 0


def cmd_forget(args) -> int:
    gone = bridge.store().forget(namespace_of(args), args.ids)
    print("\n".join(gone) or "nothing matched.")
    return 0


def cmd_status(args) -> int:
    store = bridge.store()
    namespace = namespace_of(args)
    where = getattr(store, "qualified", None) or getattr(store, "path", "?")
    print(f"store:     {'hotdata' if getattr(store, 'database', None) else 'file'}")
    print(f"location:  {where}")
    if getattr(store, "database", None):
        print(f"database:  {store.database}")
    print(f"namespace: {namespace}")
    print(f"mode:      {bridge.option('mode', 'mirror')}")
    try:
        print(f"records:   {len(store.current(namespace, limit=1000))}")
    except Exception as error:
        print(f"records:   unreachable — {error}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="memory", description=__doc__)
    parser.add_argument("--scope", help="namespace such as claude/my-project")
    parser.add_argument("--budget", type=int, default=2000)
    sub = parser.add_subparsers(dest="command", required=True)

    recall = sub.add_parser("recall", help="print the records that answer a question")
    recall.add_argument("query")
    recall.add_argument("--limit", type=int, default=10)
    recall.set_defaults(run=cmd_recall)

    listing = sub.add_parser("list", help="print every current record")
    listing.add_argument("--limit", type=int, default=100)
    listing.set_defaults(run=cmd_list)

    remember = sub.add_parser("remember", help="write one fact")
    remember.add_argument("content")
    remember.add_argument("--subject", default="")
    remember.add_argument("--kind", default="fact",
                          choices=["fact", "profile", "procedure"])
    remember.add_argument("--query", default="",
                          help="the SQL that reproduces this, when it carries a figure")
    remember.add_argument("--tag", action="append", default=[], dest="tags")
    remember.add_argument("--source", action="append", default=[], dest="sources")
    remember.add_argument("--actor", default="claude-code")
    remember.add_argument("--force", action="store_true")
    remember.set_defaults(run=cmd_remember)

    sup = sub.add_parser("supersede", help="replace a record that turned out wrong")
    sup.add_argument("id")
    sup.add_argument("content")
    sup.add_argument("--subject", default="")
    sup.add_argument("--kind", default="", choices=["", "fact", "profile", "procedure"])
    sup.add_argument("--query", default="")
    sup.add_argument("--actor", default="claude-code")
    sup.set_defaults(run=cmd_supersede)

    forget = sub.add_parser("forget", help="delete records outright")
    forget.add_argument("ids", nargs="+")
    forget.set_defaults(run=cmd_forget)

    status = sub.add_parser("status", help="print where memory lives and how much")
    status.set_defaults(run=cmd_status)

    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except HotdataError as error:
        # A CLI failure is a message, not a stack trace: the agent reading this
        # should learn what to fix, not what the plugin is made of.
        print(f"memory: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
