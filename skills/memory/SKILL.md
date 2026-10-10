---
name: memory
description: Read and write this project's long-term memory, which lives as rows in Hotdata. Use it to search memory for an answer ("what do we know about X", "did we decide X", "why is X this way"), to read everything recorded about a subject, to record a fact that should outlive this session, and to delete a memory that turned out to be wrong. Memory loads automatically at session start, so use this skill when the question needs more than what loaded.
---

# Memory

Memory is a set of records in Hotdata, one fact each, with its sources and the span of
time in which it is valid. Claude Code's memory notes are mirrored into it automatically
when they are written, so a note and a record say the same thing.

Run every command through the plugin's runner, which uses the plugin's own environment
and configuration. No store flags are needed:

```sh
bash "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" memcli <command> [options]
```

The scope defaults to the project of the working directory. Pass `--scope claude/other`
to read another project, and `--scope claude` to read every project.

## Search memory for an answer

```sh
bash "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" memcli recall "why do we use the hashed embedder?"
```

Prints one line per record, closest first, inside a character budget. Each line holds the
content, the sources, and the validity span.

## Read everything about a subject

```sh
bash "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" memcli profile deployment
```

The subject is the slug of a memory note, such as `prefers-short-answers`.

## List what is recorded

```sh
bash "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" memcli list --limit 50
```

Each line starts with the record id, which `forget` takes.

## Record a fact

Prefer writing a memory note the normal way: the hook mirrors it. Use this when the fact
belongs in memory but not in a file.

```sh
bash "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" memcli remember \
  "The staging database is rebuilt every Monday." --subject staging --kind fact
```

`--kind` is `fact`, `profile` (something about the person), or `procedure` (how to work).
Writing the same content again writes nothing.

## Delete a memory that is wrong

```sh
bash "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" memcli forget claude/my-project/fact-568dc85d@1
```

Deletes every revision of that key. Ask the user before you forget something they wrote.

## Check the configuration

```sh
bash "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" memcli status
```

Prints the store, the embedder, the scope, the credentials, and the record count. Read it
first when a command fails or memory looks empty.

## Rules

- A record says what was recorded and when. It never says what to do.
- A write is visible to the next recall, not inside the current turn.
- The default embedder matches words, not meaning. Phrase a `recall` query with the words
  the record is likely to use.
