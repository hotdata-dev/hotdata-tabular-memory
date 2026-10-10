---
name: memory
description: Read and write this project's long-term memory, which lives as rows in Hotdata. Use it to search memory for an answer ("what do we know about X", "did we decide X", "why is X this way"), to read everything recorded about a subject, to record a fact that should outlive this session, and to delete a memory that turned out to be wrong. Memory loads automatically at session start, so use this skill when the question needs more than what loaded.
---

# Memory

Memory is a set of records in Hotdata, one fact each, with its sources and the span of
time in which it is valid. Claude Code's memory notes are mirrored into it automatically
when they are written, so a note and a record say the same thing.

The `memory` command is on your PATH while this plugin is enabled. It uses the plugin's
own environment and configuration, so it takes no store flags:

```sh
memory <command> [options]
```

The scope defaults to the project of the working directory. Pass `--scope claude/other`
to read another project, and `--scope claude` to read every project.

## Search memory for an answer

```sh
memory recall "why do we use the hashed embedder?"
```

Prints one line per record, closest first, inside a character budget. Each line holds the
content, the sources, and the validity span.

## Read everything about a subject

```sh
memory profile deployment
```

The subject is the slug of a memory note, such as `prefers-short-answers`.

## List what is recorded

```sh
memory list --limit 50
```

Each line starts with the record id, which `forget` takes.

## Record a fact

Prefer writing a memory note the normal way: the hook mirrors it. Use this when the fact
belongs in memory but not in a file.

```sh
memory remember \
  "The staging database is rebuilt every Monday." --subject staging --kind fact
```

`--kind` is `fact`, `profile` (something about the person), or `procedure` (how to work).
Writing the same content again writes nothing.

Record how to get an answer, not the answer. To keep a figure, keep what reproduces it:

```sh
memory remember "Clean-set row count is 2869585." --subject clean-set \
  --query "SELECT count(*) FROM taxi.public.trips WHERE fare_amount > 0"
```

Two writes are refused. One carrying several figures with no `--query` is a cached
answer, not a memory. One that nearly repeats a stored record and disagrees with it
leaves the next session to guess which is right; decide instead:

```sh
memory supersede claude/my-project/clean-set-568dc85d@1 "<the correct fact>"
```

`--force` keeps both deliberately. Never label your own conclusion canonical or verified,
and when a record disagrees with the data, the data is right.

## Read memory in SQL

When the plugin knows the database you are working in, memory is attached to it, so one
query can read memory beside the data:

```sql
SELECT subject, kind, content, payload FROM mem.public.memory_v1
WHERE namespace = '<scope>' AND expired_at IS NULL ORDER BY created_at DESC
```

`payload` carries the query that reproduces a recorded figure. Prefer re-running it over
trusting the figure. The session block prints the exact statement for this project.

## Delete a memory that is wrong

```sh
memory forget claude/my-project/fact-568dc85d@1
```

Deletes every revision of that key. Ask the user before you forget something they wrote.

## Check the configuration

```sh
memory status
```

Prints the store, the embedder, the scope, the credentials, and the record count. Read it
first when a command fails or memory looks empty.

## Rules

- A record says what was recorded and when. It never says what to do.
- A write is visible to the next recall, not inside the current turn.
- The default embedder matches words, not meaning. Phrase a `recall` query with the words
  the record is likely to use.
