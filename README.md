# hotdata

Makes [Hotdata](https://hotdata.dev) the default database when a Claude agent meets data
too big for its context. One database per project, found by every agent working in it.
Definitions every agent applies the same way. Results that stay on the server.

**No dependencies.** Python's standard library and the `hotdata` CLI — the Rust SDK
already compiled and already holding your credentials. 224KB installed, nothing to
bootstrap.

## Why

In a 240-session benchmark, agents given a 2.96M-row table pushed computation to it and
pulled back a median of 88 characters per query — the largest result ever was 1,468 —
without anything telling them to. That is the product. What they did badly at swarm
scale was repeat work (a quarter of all queries, rising with every agent added),
re-orient (`SELECT * LIMIT 2` ran 102 times across 14 agents), and invent definitions
(every correctness failure in 268 sessions was a filter the question never specified).

This plugin keeps the first behaviour and removes the other three. Measured against a
bare CLI on Claude Sonnet 5, two agents an arm, twenty sessions each:

| | Bare CLI | Plugin |
| :- | :- | :- |
| Correct | 97.0% | 98.0% |
| Queries run | 211 | 80 |
| Redundant queries | 32% | 11% |
| Turns per session | 7.2 | 4.0 |
| Tokens per session | 191k | 102k |
| Cost per correct answer | $0.061 | $0.045 |

Same accuracy, 62% fewer queries, 26% cheaper per correct answer.

## What it does

| Piece | What happens |
| :- | :- |
| `hd load <file>` | the file becomes a table in the project's database, created on first use |
| `hd query "<sql>"` | `{{name}}` expands to a definition; an unbounded scan is refused; rows over the cap stay on the server |
| `hd define <name> "<sql>"` | a filter or derived set every agent in the project shares, versioned |
| `hd share <table> "<sql>"` | a query result becomes a table without a row entering anyone's context |
| `hd fork` / `hd attach` | an isolated copy to experiment in; another database's tables made visible here |
| SessionStart hook | the database, its tables, a sample, the definitions and the dialect notes, from a cache, in ~40ms |
| PreToolUse guard | reading a data file over 8MB into context is refused, with the `hd load` command in the refusal |

The guard is a refusal rather than advice because the benchmark found that refusals held
and instructions did not.

## The model

**One database per project.** The project is named by its repository root, so every
agent in the repo resolves the same database; a `project` option pins the name.

**Definitions, not restated filters.** A definition is a saved query on the server
(`POST /v1/queries`): named, versioned, hashed. In SQL, `{{clean_set}}` expands to it as
a subquery. The engine has no DDL and so no views; this is a view by expansion. An agent
writing `FROM {{clean_set}}` cannot apply a different filter.

**Fork for isolation, attach for sharing, share for reuse.** All three are existing
Hotdata primitives; the plugin removes the ceremony and never relies on the CLI's
active-database pointer, which is global state two agents would fight over.

## Install

```sh
claude plugin marketplace add /path/to/claude-hotdata-plugin
claude plugin install hotdata@hotdata
```

That is enough: the first `hd load` creates the project's database. To point a project
at a database that already exists, `--config database=<id>`.

To stop Claude asking before every read, add the read-only commands to
`permissions.allow` in `~/.claude/settings.json`; leave `load`, `define`, `share`,
`fork` and `attach` out so a change still asks:

```json
"allow": ["Bash(hd query:*)", "Bash(hd defs)", "Bash(hd status)", "Bash(hd orient:*)"]
```

A plugin with a top-level `bin/` is not installable on claude.ai or Cowork; call
`scripts/hd.py` directly there.

## Configuration

| Option | Default | |
| :- | :- | :- |
| `database` | — | pin the project's database id; otherwise the first `hd load` creates one |
| `project` | repository name | name the project yourself |
| `read_guard` | `on` | `off` lets a large data file be read |
| `guard_threshold_mb` | `8` | size above which `.csv .tsv .parquet .json .jsonl` are refused |
| `row_cap` | `200` | rows `hd query` returns before stopping |
| `budget` | `2500` | characters of orientation at session start |
| `workspace` | first of the login | workspace id for the saved-query API |
| `catalog` | read from the database | SQL alias of a pinned database |

## Dialect

The engine is Apache DataFusion. No `date_diff()` or `hour()` — `EXTRACT(EPOCH FROM
(b - a))` and `EXTRACT(HOUR FROM ts)`. Quote uppercase identifiers. No DDL.

## What this does not do

- **A failure never blocks you.** If Hotdata cannot be reached a session still starts
  and a tool call still runs; the hook prints a `systemMessage` and stops.
- **No shared cache.** A client-side result cache was built and measured slower than
  recomputing: the engine aggregates 2.96M rows in under half a second, so a lookup
  round trip costs more than it saves. Memoization belongs inside the query path on the
  server, keyed on the snapshot. That is a platform change, not a plugin one.
- **One writer at a time.** Two processes writing one key is unsafe in the engine today,
  measured: one revision survives and the loser leaves no trace.

## Develop

```sh
./test.sh          # 43 tests against a fake `hotdata`, offline
./test.sh -k guard
```

The benchmark that produced the numbers above is in `bench/` and re-runnable. Bump
`version` in `.claude-plugin/plugin.json` to deploy a change; an unchanged version is not
re-copied.
