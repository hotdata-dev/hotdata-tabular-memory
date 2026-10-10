"""The memory commands an agent runs: recall, profile, remember, forget, and status.

Every command reads the store that the plugin is configured for, so no store flags
are needed. A scope defaults to the project of the working directory.
"""

from __future__ import annotations

import argparse
import os
import sys

import bridge
from hotmemory import Fact, Filter, normalize
from hotmemory.contract import RECALLED_KINDS, fit_lines, render_line


def scope_of(args) -> tuple[str, ...]:
    """Return the scope to read or write.

    With no `--scope`, this resolves the same way the hooks do, so a command run from
    a subdirectory, or from /tmp, still reaches the project's own memory.
    """
    if args.scope:
        return tuple(bridge.label(part) for part in args.scope.split("/"))
    return bridge.scope_for(os.getcwd())


def cmd_recall(args) -> int:
    with bridge.memory() as memory:
        _, block = memory.recall(args.query, [scope_of(args)], budget=args.budget, k=args.limit)
    print(block or "no memory matches that question.")
    return 0


def cmd_profile(args) -> int:
    with bridge.memory() as memory:
        _, block = memory.profile(args.subject, [scope_of(args)], budget=args.budget)
    print(block or f"no memory about {args.subject!r}.")
    return 0


def cmd_list(args) -> int:
    scope = scope_of(args)
    with bridge.memory() as memory:
        records = memory.store.list(scope, Filter(kind=RECALLED_KINDS), limit=args.limit)
    if not records:
        print(f"no memory under {'/'.join(scope)}.")
        return 0
    lines = [f"{record.id}  {render_line(record)}" for record in records]
    print("\n".join(lines[: fit_lines(lines, args.budget)]))
    return 0


def cmd_remember(args) -> int:
    """Write one fact, after two checks that the content is worth keeping.

    A record carrying several figures and no query is a cached answer: it was true of
    one query on one day, and every later session inherits it. A record that nearly
    repeats one already stored is a second opinion, and a store holding both leaves the
    next session to guess. Neither is written.
    """
    payload = {}
    if args.query:
        payload["query"] = args.query
    if not args.query and bridge.reads_as_a_conclusion(args.content):
        found = ", ".join(bridge.figures(args.content)[:5])
        print(
            f"refused: this records figures ({found}) with no query to reproduce them.\n"
            "A figure was true of one query on one day. Record the query that produces "
            "it, with --query \"<sql>\", or record the method instead of the result.",
            file=sys.stderr,
        )
        return 2
    fact = Fact(
        kind=args.kind,
        subject=args.subject,
        content=args.content,
        tags=tuple(args.tags),
        sources=tuple(args.sources),
        payload=payload,
    )
    scope = scope_of(args)
    with bridge.memory(write=True) as memory:
        if not args.force:
            near = [
                hit for hit in memory.candidates(fact, [scope], k=3)
                if hit.distance is not None and hit.distance <= bridge.NEAR
                and normalize(hit.record.content) != normalize(fact.content)
            ]
            if near:
                hit = near[0]
                print(
                    f"refused: this nearly repeats a record already stored, and the two "
                    f"disagree.\n\n  stored: {hit.record.id}\n  {hit.record.content[:300]}\n\n"
                    "Decide which is right. Replace the stored one with\n"
                    f"  memory supersede {hit.record.id} \"<the correct fact>\"\n"
                    "or keep both deliberately with --force.",
                    file=sys.stderr,
                )
                return 2
        ids = memory.remember([fact], scope, actor=args.actor)
    print("\n".join(ids))
    return 0


def cmd_supersede(args) -> int:
    """Replace the current revision of the record named by `id`."""
    scope = scope_of(args)
    namespace, _, rest = args.id.rpartition("/")
    key = rest.split("@")[0]
    fact = Fact(kind=args.kind, subject=args.subject, content=args.content,
                payload={"query": args.query} if args.query else {})
    with bridge.memory(write=True) as memory:
        new_id = memory.supersede(tuple(namespace.split("/")), key, fact,
                                  actor=args.actor)
    print(new_id)
    return 0


def cmd_forget(args) -> int:
    with bridge.memory(write=True) as memory:
        removed = memory.forget([scope_of(args)], ids=args.ids)
    print("\n".join(removed) or "nothing matched.")
    return 0


def cmd_status(args) -> int:
    store = bridge.option("store", "local")
    scope = scope_of(args)
    print(f"store:    {store}")
    print(f"embedder: {bridge.option('embedder', 'hashed')}")
    print(f"scope:    {'/'.join(scope)}")
    if store == "hotdata":
        bridge.credentials()
        print(f"workspace: {os.environ.get('HOTDATA_WORKSPACE', '(first of the login)')}")
        print(f"api key:   {'set' if os.environ.get('HOTDATA_API_KEY') else 'MISSING'}")
    else:
        print(f"file:     {bridge.option('memory_file') or bridge.data_dir() / 'memory.json'}")
    with bridge.memory() as memory:
        records = memory.store.list(scope, Filter(kind=RECALLED_KINDS), limit=1000)
    print(f"records:  {len(records)} under this scope")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="memory", description=__doc__)
    parser.add_argument("--scope", help="namespace such as claude/my-project")
    parser.add_argument("--budget", type=int, default=2000, help="characters to print")
    sub = parser.add_subparsers(dest="command", required=True)

    recall = sub.add_parser("recall", help="print the records that answer a question")
    recall.add_argument("query")
    recall.add_argument("--limit", type=int, default=10)
    recall.set_defaults(run=cmd_recall)

    profile = sub.add_parser("profile", help="print every current record about a subject")
    profile.add_argument("subject")
    profile.set_defaults(run=cmd_profile)

    listing = sub.add_parser("list", help="print every current record under the scope")
    listing.add_argument("--limit", type=int, default=100)
    listing.set_defaults(run=cmd_list)

    remember = sub.add_parser("remember", help="write one fact")
    remember.add_argument("content")
    remember.add_argument("--subject", default="")
    remember.add_argument("--kind", default="fact", choices=["fact", "profile", "procedure"])
    remember.add_argument("--tag", action="append", default=[], dest="tags")
    remember.add_argument("--source", action="append", default=[], dest="sources")
    remember.add_argument("--actor", default="claude-code")
    remember.add_argument("--query", default="",
                          help="the SQL that reproduces this, when it carries a figure")
    remember.add_argument("--force", action="store_true",
                          help="write even though it nearly repeats a stored record")
    remember.set_defaults(run=cmd_remember)

    sup = sub.add_parser("supersede", help="replace a record that turned out wrong")
    sup.add_argument("id", help="the record id to replace")
    sup.add_argument("content")
    sup.add_argument("--subject", default="")
    sup.add_argument("--kind", default="fact", choices=["fact", "profile", "procedure"])
    sup.add_argument("--query", default="")
    sup.add_argument("--actor", default="claude-code")
    sup.set_defaults(run=cmd_supersede)

    forget = sub.add_parser("forget", help="delete every revision of the keys of these ids")
    forget.add_argument("ids", nargs="+")
    forget.set_defaults(run=cmd_forget)

    status = sub.add_parser("status", help="print the store, scope, and record count")
    status.set_defaults(run=cmd_status)

    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
