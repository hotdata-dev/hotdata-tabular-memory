# hotdata-tabular-memory

A Claude Code plugin that gives agents a shared SQL database for data too big for
context. Each project gets one [Hotdata](https://hotdata.dev) database. Every agent in
the project finds it, uses the same named filters, and keeps large results on the server.

## Install

```sh
/plugin marketplace add hotdata-dev/claude-plugins
/plugin install hotdata-tabular-memory@hotdata
```

You need a Hotdata account, logged in on the machine. The first `hd load` creates the
project's database. Nothing else to set up.

## How it works

| Piece | What it does |
| :- | :- |
| `hd` command | Loads files, runs SQL, manages shared definitions. On `PATH` while the plugin is enabled. |
| SessionStart hook | Tells the agent the database, its tables, a sample, and the definitions. Served from a cache in about 40ms. |
| PreToolUse guard | Refuses to read a data file over 8MB into context, and suggests `hd load` instead. |
| `tabular-memory` skill | Tells Claude when and how to use `hd`. |

**One database per project.** The repository root names the project, so every agent in
the repo resolves the same database.

**Definitions instead of repeated filters.** A definition is a named, versioned query
stored on the server. In SQL, `{{name}}` expands to it as a subquery. Two agents that
both write `FROM {{clean_trips}}` cannot apply different filters.

**Results stay on the server.** `hd query` stops at a row cap. `hd share` saves a result
as a table, so other agents can read it without the rows entering anyone's context.

## Commands

```sh
hd load data/trips.parquet                 # file or URL becomes a table
hd query "SELECT count(*) FROM {{clean}}"  # run SQL
hd batch "a: SELECT ..." "b: SELECT ..."   # several queries in one call
hd define clean "SELECT * FROM trips WHERE fare > 0"
hd defs                                    # list definitions
hd share top_zones "SELECT ..."            # save a result as a table
hd fork                                    # isolated copy to experiment in
hd attach <database-id>                    # read another database's tables
hd status                                  # project, database, tables, definitions
hd orient                                  # what an agent sees at session start
```

To skip permission prompts for read-only commands, add this to `permissions.allow` in
`~/.claude/settings.json`. Commands that change data still ask.

```json
"allow": ["Bash(hd query:*)", "Bash(hd batch:*)", "Bash(hd defs)", "Bash(hd status)", "Bash(hd orient:*)"]
```

## Configuration

Set options with `--config <option>=<value>` at install, or in the plugin settings.

| Option | Default | Effect |
| :- | :- | :- |
| `database` | none | Use an existing database id instead of creating one. |
| `project` | repository name | Name the project yourself. |
| `read_guard` | `on` | `off` disables the large-file guard. |
| `guard_threshold_mb` | `8` | Size above which `.csv .tsv .parquet .json .jsonl` reads are refused. |
| `row_cap` | `200` | Rows `hd query` returns before it stops. |
| `budget` | `2500` | Characters injected at session start. |
| `workspace` | first workspace | Workspace id. |
| `catalog` | from the database | SQL alias of a pinned database. |

## SQL dialect

The engine is Apache DataFusion. Use `EXTRACT(EPOCH FROM (b - a))` instead of
`date_diff()`, and `EXTRACT(HOUR FROM ts)` instead of `hour()`. Quote uppercase
identifiers. There is no DDL.

## Results

Benchmark on Claude Opus 5: 50 questions over a 2.96M-row taxi table, against Claude
with the same parquet file on disk and no plugin.

| | No plugin | Plugin |
| :- | :- | :- |
| Correct | 95.0% | 97.5% |
| Turns per session | 5.0 | 3.4 |
| Tokens per session | 138k | 97k |
| Cost per correct answer | $0.053 | $0.045 |

The gain depends on the model batching queries. On Haiku 5.5 the savings were much
smaller. The data was 48MB on local disk, the case that favours working without a
database. Details are in `bench/README.md`.

## Limits

- **Failures never block.** If Hotdata is unreachable, sessions still start and tool
  calls still run.
- **One writer at a time.** Two processes writing the same definition at once is unsafe.
  One write wins and the other is lost.

## Develop

Requires Python 3 standard library only.

```sh
./test.sh            # offline tests against a fake backend
./test.sh -k guard   # one area
```

| Path | Contents |
| :- | :- |
| `scripts/` | `hd` implementation, hooks, and API client |
| `skills/tabular-memory/` | The skill Claude reads |
| `hooks/hooks.json` | Hook registration |
| `bench/` | Single-agent benchmark |
| `swarm/` | Multi-agent benchmark |

A plugin with a top-level `bin/` cannot install on claude.ai or Cowork. Call
`scripts/hd.py` directly there.
