# hotdata-memory

Keeps an agent's memory as an append-only table in the [Hotdata](https://hotdata.dev)
database the project already queries, so one statement reads memory beside the data.

**No dependencies.** Python's standard library and the `hotdata` CLI, which is the Rust
SDK already compiled and already holding your credentials. No virtual environment, no
bootstrap, no first-session delay.

## What it does

| Hook | What happens |
| :- | :- |
| `SessionStart` | Prints the project's records as `additionalContext`, from a cached block, so a session never waits on the network |
| `PostToolUse` on `Write`/`Edit` | In mirror mode, a memory note that was just written becomes a record |

A skill, `memory`, gives Claude the commands to search, record, correct and forget.

## The record

One table, `agent_memory`, never updated in place:

| Column | |
| :- | :- |
| `id` | `<subject>-<hash of the content>`, so writing the same fact twice stores it once |
| `namespace` | `<scope prefix>/<project>` |
| `subject` | what the record is about; two records on one subject is a conflict |
| `kind` | `fact`, `profile`, or `procedure` |
| `content` | the record |
| `query` | the SQL that reproduces a figure, when the record carries one |
| `supersedes` | the id this record replaces |
| `tags`, `sources`, `actor`, `created_at` | provenance |

The current set is every record nothing supersedes. Corrections append, so the history
stays readable — which is the only reason an agent's wrong answer could be diagnosed at
all when this was benchmarked.

## Two refusals

These are checks, not instructions, because instructions did not hold:

- **A write carrying several figures with no `--query`.** A figure was true of one query
  on one day; a wrong one recorded as fact is worse than no memory, because every later
  answer inherits it.
- **A second record on a subject that already has one saying something different.**
  Decide with `memory supersede` rather than storing both.

`--force` overrides the second when both are genuinely true.

## Install

```sh
claude plugin marketplace add /path/to/claude-hotdata-plugin
claude plugin install hotdata-memory@hotdata --config database=<your database id>
```

Leave `database` empty to keep memory in a local JSON file instead.

To stop Claude asking before every read, add the read-only commands to
`permissions.allow` in `~/.claude/settings.json`. Leave `remember`, `supersede` and
`forget` out, so a change still asks:

```json
"allow": ["Bash(memory recall:*)", "Bash(memory list:*)", "Bash(memory status)"]
```

Because the plugin has a top-level `bin/`, claude.ai and Cowork will not install it.
Drop the directory and call `scripts/memcli.py` instead if you need to distribute there.

## Configuration

| Option | Default | What it sets |
| :- | :- | :- |
| `database` | — | The Hotdata database memory lives in. Empty means a local file |
| `mode` | `mirror` | `mirror` follows Claude's memory files; `standalone` makes the table the only memory |
| `project` | repository name | Names the project instead of letting the directory name it |
| `scope_prefix` | `claude` | First label of the namespace |
| `budget` | `2000` | Characters of memory loaded at session start |
| `catalog` | read from the database | SQL alias of the database |
| `memory_root` | `~/.claude/projects` | Where a written `.md` counts as a note, in mirror mode |
| `memory_file` | plugin data dir | Where memory goes with no database |

## Commands

```sh
memory status
memory list
memory recall "why do we use the hashed embedder?"
memory remember "Staging is rebuilt every Monday." --subject staging
memory remember "Row count is 2869585." --subject rows --query "SELECT count(*) FROM t"
memory supersede <id> "<the correct fact>"
memory forget <id>
```

## What this does not do

- **A failure never blocks you.** If the database cannot be reached, a session still
  starts and an edit still completes; the hook prints a `systemMessage` and stops.
- **Search is word matching.** The store is small and the session block carries it all;
  a record is found by the words it uses, not by meaning.
- **One writer at a time.** Two processes writing one key is unsafe in the engine today
  — measured, not assumed — so concurrent agents should read freely and write from one
  place.

## Develop

```sh
./test.sh          # builds a pytest venv outside this directory and runs the tests
./test.sh -k clash
```

Bump `version` in `.claude-plugin/plugin.json`, then `claude plugin update
hotdata-memory@hotdata`; an unchanged version is not re-copied.
