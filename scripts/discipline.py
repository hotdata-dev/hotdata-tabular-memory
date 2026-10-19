"""What a query is allowed to bring back into an agent's context.

The CLI caps a preview at 10,000 rows, which is a human limit. An agent's is lower,
and an unbounded scan is refused outright: the right move is to aggregate, or to
materialise the result as a table with `tabular-memory share` and query that.
"""

from __future__ import annotations

import re

AGGREGATE = re.compile(r"\b(count|sum|avg|min|max|median|approx_median|stddev|"
                       r"variance|array_agg|string_agg)\s*\(|\bgroup\s+by\b", re.I)
LIMIT = re.compile(r"\blimit\s+\d+", re.I)
STAR = re.compile(r"\bselect\s+(?:\w+\.)?\*", re.I)
WRITE = re.compile(r"^\s*(?:(?:--[^\n]*\n|/\*.*?\*/)\s*)*insert\b", re.I | re.S)


def classify(sql: str) -> str:
    """Return `write`, `aggregate`, `projection` or `unbounded`.

    An INSERT brings back a row count, not rows, so no cap applies to it.
    """
    if WRITE.search(sql):
        return "write"
    text = " ".join(sql.split())
    if AGGREGATE.search(text):
        return "aggregate"
    if LIMIT.search(text):
        return "projection"
    return "unbounded"


def refusal(sql: str, cap: int, total: int = 0) -> str:
    star = bool(STAR.search(sql))
    what = "a `SELECT *` with no LIMIT" if star else "a scan with no aggregate and no LIMIT"
    size = f" It matches {total:,} rows." if total else ""
    return (f"refused: this is {what}, which would pull every matching row into context.{size}\n"
            f"Aggregate it, add `LIMIT {cap}`, or keep the rows out of context:\n"
            f"  tabular-memory share <table> \"<the query>\"     # materialise it, then query the table\n"
            f"  tabular-memory query \"<the query>\" --out rows.csv   # every row to a file\n"
            f"Pass --all to stream the whole result anyway.")


def trim(rows: list[list], total: int, cap: int) -> tuple[list[list], str]:
    """Return at most `cap` rows and a footer when some were left out."""
    if total <= cap and len(rows) <= cap:
        return rows, ""
    shown = rows[:cap]
    return shown, (f"\n{len(shown)} of {total} rows — the rest stayed on the server. "
                   f"Aggregate, or `tabular-memory share <table> \"<sql>\"` to materialise.")
