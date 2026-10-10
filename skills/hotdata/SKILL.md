---
name: hotdata
description: Work with data that is too big for context — a CSV, parquet or JSON file over a few MB, a table with thousands of rows, a question that needs SQL over a dataset, or results that several agents need to share. Use it to load a file into Hotdata ("load this into a database"), to query it ("how many rows", "top 10 by", "average per group"), to name a filter every agent should apply the same way, and to keep a result on the server instead of pasting rows. Memory of the project's database and definitions loads at session start; reach for this when you need to load, query, define or share.
---

# Hotdata

The data stays on the server. A question goes to it as SQL and an answer comes back —
one number, a few rows, never the table. One database per project; every agent working
in the project finds the same one.

The `hd` command is on your PATH while this plugin is enabled.

## Load a file

```sh
hd load data/trips.parquet            # creates the project database on first use
hd load data/zones.csv --table zones  # name the table; default is the file's stem
hd load https://example.com/trips.parquet   # the server fetches it; nothing passes through here
```

Takes a few seconds for 50MB and prints the schema, row count, time ranges, a sample,
the definitions, and results already computed. After that, do not read the file. Query it.
If a file is remote, give the URL rather than downloading it first.

## Ask several questions at once

Every call costs a turn, and a turn re-sends everything you know — about 22,000 tokens
each time, whatever the call does. **If you have more than one question, ask them in one
call.** That is where almost all of the saving is:

```sh
hd batch "q11: SELECT count(*) FROM {{clean_set}} WHERE \"PULocationID\" = 132 AND \"DOLocationID\" = 230" \
         "q12: SELECT round(avg(total_amount), 2) FROM {{clean_set}} WHERE \"PULocationID\" = 132 AND \"DOLocationID\" = 230"
```

Or a script on stdin, one statement per `;`, with `-- name` lines as labels:

```sh
hd batch <<'SQL'
-- rows
SELECT count(*) FROM {{clean_set}};
-- top_pickup
SELECT z."Zone", count(*) AS n FROM {{clean_set}} t JOIN taxi.public.zones z ON t."PULocationID" = z."LocationID" GROUP BY 1 ORDER BY n DESC LIMIT 1;
SQL
```

Each result prints under its label. One failing or refused query does not stop the
rest, and does not discard the answers beside it — read every label before deciding
anything is missing.

## Ask a question

```sh
hd query "SELECT count(*) FROM taxi.public.trips WHERE fare_amount > 0"
hd query "SELECT \"PULocationID\", count(*) AS n FROM taxi.public.trips GROUP BY 1 ORDER BY n DESC LIMIT 10"
```

Aggregate, or add a `LIMIT`. A `SELECT *` with no `LIMIT` is refused, and anything over
the row cap is cut with a footer — the rows are still on the server, and `hd share`
keeps them there as a table. `--all` streams everything as CSV when you truly need it.

## Name a definition

A filter you would otherwise restate — the clean set, the active customers, last
quarter — belongs in a definition, so every agent applies the same one and nobody
invents a variant:

```sh
hd define clean_set "SELECT * FROM taxi.public.trips WHERE fare_amount > 0 AND trip_distance > 0" \
  --description "trips with a positive fare and distance"
hd query "SELECT count(*) FROM {{clean_set}}"
hd query "SELECT avg(c.total_amount) FROM {{clean_set}} AS c JOIN taxi.public.zones z ON ..."
```

`{{name}}` expands to the definition as a subquery. Defining a name again makes a new
version; the old one stays in history. `hd defs` lists them.

**If a question says "the clean set" and no definition exists, define it from the
question's own words before answering.** Do not apply a filter you were not given.

## Keep a result on the server

```sh
hd share pair_stats "SELECT \"PULocationID\" AS pu, \"DOLocationID\" AS dz, count(*) AS n, avg(total_amount) AS fare FROM {{clean_set}} GROUP BY 1, 2 HAVING count(*) >= 500"
hd query "SELECT pu, dz, fare FROM taxi.public.pair_stats ORDER BY fare DESC LIMIT 5"
```

The intermediate never enters anyone's context, and every agent in the project can
query the table.

## Work alongside other agents

```sh
hd fork                 # an isolated copy to experiment in; prints its id and snapshot
hd attach <database-id> # see another database's tables as <alias>.public.<table>
hd status               # project, database, tables, definitions
```

A fork diverges and does not merge back; put a result worth keeping into the shared
database with `hd share`.

## Dialect

The engine is Apache DataFusion. No `date_diff()` or `hour()` — use
`EXTRACT(EPOCH FROM (b - a))` for seconds and `EXTRACT(HOUR FROM ts)`. Quote uppercase
identifiers: `"PULocationID"`. `median()` works. There is no DDL: no `CREATE VIEW`,
which is what definitions are for.
