# hotdata-memory

A Claude Code plugin that keeps Claude's auto memory in [Hotdata](https://hotdata.dev)
instead of only in Markdown files, using [hotmemory](https://github.com/hotdata-dev/hotmemory).

Each memory note becomes one row in a managed Hotdata database, so memory can be queried
with SQL and joined to your own data. At the start of a session, the project's records
are loaded back into Claude's context.

## What it does

| Hook | What happens |
| :- | :- |
| `SessionStart` | Prints the project's records as `additionalContext`. Reads a cached block, so the session never waits on the network |
| `PostToolUse` on `Write`/`Edit` | A memory note that was just written becomes one fact in the store, and the cached block is rewritten |

A skill, `memory`, gives Claude the commands to search memory, read a subject, record a
fact, and forget one.

## Install

```sh
claude plugin marketplace add /path/to/claude-hotdata-plugin
claude plugin install hotdata-memory@hotdata \
  --config store=hotdata \
  --config database_name=claude-memory \
  --config workspace_id=<workspace id>
```

The first session installs `hotmemory` into the plugin's own environment in the
background and loads memory from the session after it. To do that step now:

```sh
CLAUDE_PLUGIN_DATA=~/.claude/plugins/data/hotdata-memory-hotdata \
  bash ~/.claude/plugins/cache/hotdata/hotdata-memory/*/scripts/setup.sh
```

## Configuration

| Option | Default | What it sets |
| :- | :- | :- |
| `store` | `local` | `hotdata` for a managed database, `local` for a JSON file |
| `database_name` | `claude-memory` | Database to open, or create if it is missing |
| `database_id` | — | Open this id instead of looking the name up |
| `embedder` | `hashed` | `hashed` needs no key and matches words; `openai` needs `OPENAI_API_KEY` and matches meaning |
| `scope_prefix` | `claude` | First label of the namespace. Records go under `<prefix>/<project>` |
| `budget` | `2000` | Characters of memory loaded at session start |
| `workspace_id` | first of the login | Workspace that holds the database |
| `api_key` | — | Leave empty to use `HOTDATA_API_KEY`, or the key `hotdata auth login` saved |
| `api_url` | — | Hotdata API base URL |
| `memory_root` | `~/.claude/projects` | Directory under which a written `.md` file counts as a memory note |

## How a note becomes a record

A note's frontmatter `metadata.type` picks the hotmemory kind, and the type is kept as a
tag so nothing is lost:

| Claude type | hotmemory kind |
| :- | :- |
| `user` | `profile` |
| `feedback` | `procedure` |
| `project` | `fact` |
| `reference` | `fact` |

The note's `name` is the subject, its `description` and body are the content, and the
file name and session id are the sources. `MEMORY.md` is the index that Claude rewrites
from the notes, so it is not mirrored. Writing the same note again writes nothing.

## Commands

The skill runs these; you can run them too:

`bin/memory` is on the Bash tool's PATH while the plugin is enabled, so Claude runs it as
a bare command. From your own shell, call it by path:

```sh
memory status
memory list
memory recall "why do we use the hashed embedder?"
memory profile deployment
memory remember "Staging is rebuilt every Monday." --subject staging
memory forget claude/my-project/fact-568dc85d@1
```

To stop Claude asking before every read, add the read-only commands to `permissions.allow`
in `~/.claude/settings.json`. Leave `remember` and `forget` out, so a change or a deletion
still asks:

```json
"allow": [
  "Bash(memory recall:*)",
  "Bash(memory profile:*)",
  "Bash(memory list:*)",
  "Bash(memory status)"
]
```

Because the plugin has a top-level `bin/`, claude.ai and Cowork will not install it. Drop
the directory and call `scripts/run.sh memcli` instead if you need to distribute it there.

## What this does not do

- **A failure never blocks you.** If the store cannot be reached, a session still starts
  and an edit still completes; the hook prints a `systemMessage` and stops.
- **Files stay the source of truth locally.** The store is a mirror, so nothing is lost
  if the plugin is removed. It does not yet write files back from the store.
- **One process writes at a time.** hotmemory's driver loses a revision if two processes
  put the same key, so every write takes a lock file on this machine. Two machines
  writing the same key at the same moment is still unsafe.
- **The default embedder matches words, not meaning.** Hotdata's server-side embedding
  providers only embed through a provider-backed vector index, with no endpoint that
  embeds arbitrary text, so using them would need a change in the hotmemory driver.
  Set `embedder=openai` for semantic recall today.

## Develop

```sh
./dev.sh          # builds a venv outside this directory and runs the tests
./dev.sh -k cache # pytest flags pass through
```

Bump `version` in `.claude-plugin/plugin.json` and run `claude plugin update
hotdata-memory@hotdata` to deploy a change; an unchanged version is not re-copied.
