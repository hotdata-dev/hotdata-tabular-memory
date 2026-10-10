---
name: memory
description: Read and write this project's long-term memory, which is a table in the Hotdata database the project queries. Use it to search memory for an answer ("what do we know about X", "did we decide X", "why is X this way"), to record something that should outlive this session, and to correct a record that turned out wrong. Memory loads automatically at session start, so reach for this when the question needs more than what loaded.
---

# Memory

Memory is an append-only table, `agent_memory`, in the same database as the data. A
record is never edited: a correction appends a new record naming the one it replaces,
so the current set is everything nothing supersedes and the history stays readable.

The `memory` command is on your PATH while this plugin is enabled. It takes no store
flags — the database, catalog and namespace come from the plugin's configuration.

## Search memory

```sh
memory recall "why do we use the hashed embedder?"
memory list --limit 50
```

`list` prints the record id first; `supersede` and `forget` take it.

## Read memory in SQL

Memory is a table, so one statement reads it beside the data:

```sql
SELECT subject, kind, content, query FROM <catalog>.public.agent_memory
WHERE namespace = '<scope>'
  AND id NOT IN (SELECT supersedes FROM <catalog>.public.agent_memory
                 WHERE namespace = '<scope>' AND supersedes <> '')
ORDER BY created_at DESC
```

The session block prints the exact statement for this project. `query` holds the SQL
that reproduces a recorded figure — prefer re-running it over trusting the figure.

## Record something

```sh
memory remember "Staging is rebuilt every Monday." --subject staging --kind fact
```

`--kind` is `fact`, `profile` (about the person), or `procedure` (how to work).

**Record how to get an answer, not the answer.** A schema detail, a dialect quirk, a
definition you were given, a command that worked: these stay true. A figure you
computed was true of one query on one day. To keep one, keep what reproduces it:

```sh
memory remember "Clean-set row count is 2869585." --subject clean-set \
  --query "SELECT count(*) FROM taxi.public.trips WHERE fare_amount > 0"
```

Two writes are refused:

- One carrying several figures with no `--query`. That is a cached answer, not a memory.
- One on a subject that already has a record saying something different. A store holding
  both leaves the next session to guess.

## Correct a record

```sh
memory supersede clean-set-3fe5b2fe035fd98b "<the correct fact>"
```

The old record stays in the table and drops out of the current set. `--force` on
`remember` keeps two records on one subject deliberately; `memory forget <id>` deletes
outright, history included — ask the user first.

Never label your own conclusion canonical, settled or verified. When a record disagrees
with the data, the data is right: re-run the query and supersede the record.

## Check the configuration

```sh
memory status
```

Prints the store, the table, the namespace and the record count. Read it first when a
command fails or memory looks empty.
