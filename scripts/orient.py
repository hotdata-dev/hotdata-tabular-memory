"""The orientation block: what every agent needs to know before its first query.

Built once per database and cached, so fourteen agents do not each run
`SELECT * LIMIT 2` to find out what shape the data is.
"""

from __future__ import annotations

import re

import defs
import hotdata
import project

ORIENTISH = re.compile(r"information_schema|\bdescribe\b|\bselect\s+\*.*\blimit\s+[1-5]\b", re.I)
RECENT_LIMIT = 6


def recent_results(database: str) -> list[str]:
    """Return lines for the latest distinct successful queries, with their result ids.

    A question already answered can be reused by its result id. The run record's
    `snapshot_id` is a per-run digest, not a data version, so nothing here claims a
    result is current: it is the latest run of that query, and the agent judges
    whether its tables have changed since.
    """
    try:
        runs = hotdata.query_runs(database, limit=40)
    except hotdata.HotdataError:
        return []
    seen, lines = set(), []
    for r in runs:  # newest first
        sql = " ".join((r.get("sql_text") or "").split())
        status = str(r.get("status") or "").lower()
        if (any(w in status for w in ("fail", "error", "cancel")) or r.get("error_message")
                or not sql or not r.get("result_id") or ORIENTISH.search(sql)
                or "tabular-memory:internal" in sql or r.get("sql_hash") in seen):
            continue
        seen.add(r.get("sql_hash"))
        lines.append(f"  {r['result_id']}  ({r.get('row_count', '?')} rows)  {sql[:88]}")
        if len(lines) >= RECENT_LIMIT:
            break
    return lines


DIALECT = """Dialect (Apache DataFusion): no date_diff() or hour() — use EXTRACT(EPOCH FROM (b - a))
for seconds and EXTRACT(HOUR FROM ts); quote uppercase identifiers ("PULocationID");
median() works; no DDL. Results are capped: aggregate, or `tabular-memory share <table> "<sql>"` to
keep rows on the server."""


def block(entry: dict, budget: int) -> str:
    """Return the orientation block for the project database `entry`."""
    database, catalog = entry["database"], entry["catalog"]
    lines = [
        f"Hotdata database for this project: {database} (catalog `{catalog}`).",
        f"Query it with  tabular-memory query \"<sql>\"  — tables are {catalog}.public.<table>. "
        f"In SQL, {{{{name}}}} expands to a definition below.",
        "",
    ]
    try:
        definitions = defs.all_definitions(entry['project'])
    except hotdata.HotdataError:
        definitions = {}
    if definitions:
        lines.append("Definitions (use these instead of restating a filter):")
        for name, d in sorted(definitions.items()):
            desc = d.get("description") or d.get("sql", "")[:80]
            lines.append(f"  {{{{{name}}}}} — {desc}")
        lines.append("")
    recent = recent_results(database)
    if recent:
        lines.append("Already computed in this project, newest first. If the tables it read "
                     "have not changed since, reuse the result instead of re-running it: "
                     "tabular-memory share <table> --result-id <id>")
        lines.extend(recent)
        lines.append("")
    for table in hotdata.tables(database, catalog):
        name = table["name"]
        count = f"{table['rows']:,} rows" if table["rows"] is not None else "rows unknown"
        cols = hotdata.columns(database, catalog, name)
        lines.append(f"{name} ({count})")
        lines.append("  " + ", ".join(f"{c['name']}:{c['type']}" for c in cols))
        try:
            for col, (lo, hi) in hotdata.ranges(database, catalog, name, cols).items():
                lines.append(f"  {col}: {str(lo)[:19]} to {str(hi)[:19]}")
        except hotdata.HotdataError:
            pass
        try:
            for row in hotdata.sample(database, catalog, name, 2):
                lines.append("  e.g. " + ", ".join(str(v)[:24] for v in row.values()))
        except hotdata.HotdataError:
            pass
        lines.append("")
    lines.extend(DIALECT.splitlines())
    return "\n".join(project.fit(lines, budget))


def cached(entry: dict) -> str | None:
    path = project.cache_path("orient", entry["database"])
    return path.read_text(encoding="utf-8") if path.exists() else None


def refresh(entry: dict, budget: int) -> str:
    text = block(entry, budget)
    project.write_cache("orient", entry["database"], text)
    return text


def budget() -> int:
    try:
        return max(400, int(float(project.option("budget", "2500"))))
    except ValueError:
        return 2500
