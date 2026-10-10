"""The orientation block: what every agent needs to know before its first query.

Built once per database and cached, so fourteen agents do not each run
`SELECT * LIMIT 2` to find out what shape the data is.
"""

from __future__ import annotations

import defs
import hotdata
import project

DIALECT = """Dialect (Apache DataFusion): no date_diff() or hour() — use EXTRACT(EPOCH FROM (b - a))
for seconds and EXTRACT(HOUR FROM ts); quote uppercase identifiers ("PULocationID");
median() works; no DDL. Results are capped: aggregate, or `hd share <table> "<sql>"` to
keep rows on the server."""


def block(entry: dict, budget: int) -> str:
    """Return the orientation block for the project database `entry`."""
    database, catalog = entry["database"], entry["catalog"]
    lines = [
        f"Hotdata database for this project: {database} (catalog `{catalog}`).",
        f"Query it with  hd query \"<sql>\"  — tables are {catalog}.public.<table>. "
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
    for table in hotdata.tables(database, catalog):
        name = table["name"]
        count = f"{table['rows']:,} rows" if table["rows"] is not None else "rows unknown"
        cols = hotdata.columns(database, catalog, name)
        lines.append(f"{name} ({count})")
        lines.append("  " + ", ".join(f"{c['name']}:{c['type']}" for c in cols))
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
