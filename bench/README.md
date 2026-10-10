# Memory benchmark

Does keeping Claude Code's memory in Hotdata beat keeping it in Markdown files, and
does either beat no memory at all?

## The design

Memory only does work **across** sessions — inside one session everything is already in
context. So each agent runs 20 short sessions rather than one long one, and the same 50
questions run twice: pass 1 discovers, pass 2 repeats the identical questions. An agent
whose memory works should answer pass 2 without querying the data again.

Three arms, four replicates each, twelve agents of one model:

| Arm | Memory |
| :- | :- |
| `markdown` | Claude's own memory files; plugin off |
| `hotdata` | the store is the only memory; auto memory off, plugin in standalone mode |
| `none` | no memory at all |

Both memory arms query the **same** Hotdata database for the taxi data. Only the memory
backend differs, so the comparison is about memory and not about the query engine.

Each agent gets its own `autoMemoryDirectory`, project directory name, and memory scope,
so no agent reads another's memory and your own configuration is untouched.

## The questions

50 questions over NYC yellow taxi trips for January 2024 (2,964,624 rows), in four
phases: discovery, one route at a time, comparisons, then a best route under a stated
rule. Every question has one deterministic answer, computed by `build_bank.py` with
DuckDB against the same parquet that was loaded into Hotdata. No judge model grades
anything.

`validate_bank.py` runs each truth query against Hotdata as well and reports any
disagreement, because a truth that only DuckDB agrees with would mark a correct answer
wrong. Run it after any change to the bank.

## Run it

```sh
python3 build_bank.py       # compute the ground truth
python3 validate_bank.py    # check Hotdata agrees
./orchestrate.sh            # twelve agents, three arms at a time
python3 aggregate.py        # per-arm comparison -> report.json
```

`SESSIONS` and `REPLICATES` override the defaults. One agent alone:

```sh
python3 runner.py --arm hotdata --replicate 1 --sessions 20
```

Each session is one `claude -p --output-format json` process, so cost, tokens, turns,
and latency are the harness's own measurements, not estimates.
