# Benchmark

What does it cost an agent to answer questions about data too big for its context, and
does putting the data in Hotdata beat leaving it on disk?

## The arms

| Arm | What the agent has |
| :- | :- |
| `local` | The two files and nothing else. Every tool on the machine is allowed; `hotdata` and `hd` are denied, so a mention in the skill listing cannot become a call. It reaches for DuckDB. |
| `plugin` | The data in Hotdata, the plugin enabled, and `hd` on PATH. Orientation at session start, shared definitions, batching, refusals. |
| `bare` | The Hotdata CLI from a brief, no plugin. Kept for history; it measures Hotdata-without-the-plugin, not Hotdata-versus-nothing. |

`local` is the honest baseline: Claude working as it would if Hotdata did not exist.

## The questions

Fifty questions over 2,964,624 NYC yellow taxi trips for January 2024, in four phases —
schema and data quality, one route at a time, comparisons across routes, then a best
route under a stated rule. Later phases reward knowing what the earlier ones found.

Every answer is deterministic. `build_bank.py` computes it with DuckDB against the same
parquet the plugin arm loads. `validate_bank.py` then runs each truth query against
Hotdata as well, because a truth only DuckDB agrees with would mark a correct answer
wrong — that check caught 20 of 50 queries relying on functions Hotdata's engine lacks.
No judge model grades anything.

Each agent runs twenty separate `claude -p` processes of five questions, twice through
the bank, so pass 2 repeats pass 1 exactly.

## Run it

```sh
python3 build_bank.py                  # compute the ground truth
python3 validate_bank.py               # check Hotdata agrees
TAG=run MODEL=claude-opus-5 SESSIONS=20 REPLICATES=2 ARMS="local plugin" \
  export BENCH_DB=<taxi database id> BENCH_WORKSPACE=<workspace id>
  ./orchestrate.sh "$PWD/runs-myrun"   # the run
python3 aggregate.py                   # per-arm figures -> report.json
python3 compare.py                     # the arms side by side
```

One agent alone:

```sh
python3 runner.py --arm plugin --replicate 1 --out "$PWD/runs-x" --sessions 20 \
  --model claude-opus-5 --tag x
```

`aggregate.py` reads every `runs*/` directory it knows about; add a new one to its
`sources` list with a suffix, and its arms appear as `plugin_<suffix>`.

## What is measured

Cost, tokens, turns and latency come from each session's own `claude -p --output-format
json` result — nothing is modelled. The rest comes from the transcripts Claude Code
leaves in `~/.claude/projects`, matched to a run by session id so a rerun in the same
working directory cannot read the previous attempt's sessions.

`redundancy.py` extracts every query an agent ran, including the several inside an
`hd batch` call, and reports how many were repeats and how many were orientation — a
schema read or an unranked peek, as opposed to a query that answers something.

## Three traps this harness now guards against

Each of these silently invalidated a run before it was caught:

- **The model.** `orchestrate.sh` once called `runner.py` without `--model`, so every
  run used the default while the command line said otherwise. Each session now records
  the model that did the work, taken from its own `modelUsage` and ignoring the Haiku
  call that titles the session. `aggregate.py` prints it beside every arm.
- **The CLI version.** Claude Code was upgraded mid-run. Its system prompt and tool
  definitions are the prefix every turn re-reads, and that prefix is most of the token
  cost. Each session records `claude --version`; a run spanning two prints `MIXED CLI`.
- **Pricing.** A model newer than the CLI's catalog is billed at a placeholder rate —
  Haiku 5.5 was costed at the Opus rate, about 5× high. Any arm whose model the CLI
  cannot price prints `COST UNRELIABLE`, and its dollar figures must not be published.

## Results so far

Two agents an arm, twenty sessions each.

| Model | Arm | Correct | Turns | Tokens / session |
| :- | :- | :- | :- | :- |
| Opus 5 | no Hotdata | 95.0% | 5.0 | 138,345 |
| Opus 5 | with the plugin | 97.5% | **3.4** | **97,408** |
| Haiku 5.5 | no Hotdata | 97.5% | 5.2 | 188,229 |
| Haiku 5.5 | with the plugin | 96.0% | 5.1 | 164,122 |

On Opus the plugin cuts turns 32% and tokens 30%. On Haiku 5.5 it cuts 2% and 13%, and
loses 1.5 points of accuracy. The transcripts say why: Haiku 5.5 calls `hd batch` as
often as Opus does in absolute terms, 42 times against 40, but makes 70% more calls
overall — it uses the affordance and keeps working the old way alongside it. The plugin
removes turns an agent would otherwise spend; it cannot remove turns it spends anyway.

Haiku 5.5 also answers 97.5% unaided, so there was no accuracy headroom to recover.

**Unexplained:** Haiku 5.5 uses more tokens per session than Opus 5 on the same work.
It may be real, or an artefact of how an unrecognised model is handled. Do not quote it
until someone has checked.
