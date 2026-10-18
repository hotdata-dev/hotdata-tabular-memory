# hotdata-tabular-memory

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

This plugin keeps the first behaviour and removes the other three. Measured against
two alternatives on Claude Opus 5, two agents each, twenty sessions, the same fifty
questions twice. The baseline is Claude with **no Hotdata at all** — the parquet on disk
and whatever the machine has, which turned out to be DuckDB.

| | No Hotdata | Hotdata CLI | Plugin v2.1 |
| :- | :- | :- | :- |
| Correct | 95.0% | 97.0% | 97.5% |
| Right then wrong | 3 | 2 | 0 |
| Turns / session | 5.0 | 7.2 | 3.4 |
| Tokens / session | 138k | 191k | 97k |
| Tokens / correct answer | 29,125 | 39,414 | 19,981 |
| $ / correct answer | $0.053 | $0.061 | $0.045 |
| Seconds / session | 21s | 33s | 15s |

Against working locally with no database: the same cost in dollars, **32% fewer turns, 30% fewer tokens and 29% less wall time**, with three accuracy points and three right-then-wrong flips recovered. Against the raw Hotdata CLI the gap is wider on every axis.

Two things that result depends on, both measured:

**The benchmark is 48MB on the agent's own machine** — the case most favourable to the
local baseline, where DuckDB reads the parquet directly with no network, auth or
transfer. Data larger than the machine, shared between agents, or already loaded is
outside what it tests.

**The gain depends on the model batching.** Repeating the run on Haiku 5.5 cut turns by
2% and tokens by 13%, against 32% and 30% on Opus 5, and lost 1.5 points of accuracy.
Haiku 5.5 calls `hd batch` as often as Opus in absolute terms but makes 70% more calls
overall — it uses the affordance and keeps working the old way beside it. It also
answers 97.5% of these questions unaided, so there was no accuracy headroom to recover.
`bench/README.md` has the figures.

**Agents sharing work is tested separately**, in `swarm/`: many agents whose tasks
overlap, where the shared work is classification an agent pays to redo. In the pilot,
agents sharing a store reused every label already recorded, but the plugin cost more
per task than agents sharing a folder, through extra round trips rather than the store.
The [swarm benchmark report](https://claude.ai/artifact/3NYUz6iSSUFFQw9884T1zL) has the
results and what to fix next; [the shared database thesis](https://claude.ai/artifact/D2bUprTAGXCuiM2bT5m49p)
is why the benchmark exists.

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
claude plugin marketplace add /path/to/hotdata-tabular-memory
claude plugin install hotdata-tabular-memory@hotdata
```

Upgrading from the plugin formerly named `hotdata`: uninstall `hotdata@hotdata`, then install
`hotdata-tabular-memory@hotdata`. Version 3.0.0 is the rename; the behaviour is 2.1.3's.

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
./test.sh          # 56 tests against a fake `hotdata`, offline
./test.sh -k guard
```

The benchmark that produced the numbers above is in `bench/` and re-runnable. Bump
`version` in `.claude-plugin/plugin.json` to deploy a change; an unchanged version is not
re-copied.
