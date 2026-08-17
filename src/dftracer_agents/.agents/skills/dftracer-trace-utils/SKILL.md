---
name: dftracer-trace-utils
description: TOP PRIORITY — always use dftracer MCP utils tools for any trace work; never use raw bash/python/gzip scripts to read or process .pfw/.pfw.gz files
priority: critical
---

# TOP PRIORITY: Use MCP Tools for All Trace Work

**This rule overrides any default tendency to use `cat`, `python3 -c`, `gzip.open`,
`json.loads`, `grep`, or bash pipelines on trace files.**

Every time you need to read, query, filter, or count events in a `.pfw` or `.pfw.gz`
file — reach for `mcp__dftracer__view` first.  Only fall back to bash if the tool
is genuinely down or cannot do the specific task.

---

## Primary tool: `mcp__dftracer__view` — for all reading and querying

`dftracer_view` is the correct tool for **every** trace read operation.  It uses
bloom-filter indices for fast chunk-skipping, correctly resolves FH hash→filename
mappings, and handles cross-chunk events transparently.

### Query DSL — field reference

| Field | Type | Example |
|-------|------|---------|
| `cat` | string | `cat == "POSIX"` |
| `name` | string | `name == "open"` |
| `dur` | int (µs) | `dur > 1000` |
| `ph` | string | `ph == "X"` |
| `pid` | int | `pid == 1234` |
| `tid` | int | `tid == 1235` |
| `ts` | int (µs) | `ts > 1000000` |
| `args.comp` | string | `args.comp == "io"` |
| `args.fhash` | string | `args.fhash == "abc123"` |
| `args.flags` | int | `args.flags == 66` |

**Operators:** `==`  `!=`  `>`  `<`  `>=`  `<=`

**Boolean:** `and` (lowercase) · `OR` (uppercase)

**CRITICAL — strings are case-sensitive:** `"POSIX"` ✓ · `"posix"` ✗

### Confirmed working query examples

```python
# All POSIX events
mcp__dftracer__view(directory=SPLIT, query='cat == "POSIX"')

# POSIX opens only
mcp__dftracer__view(directory=SPLIT, query='cat == "POSIX" and name == "open"')

# POSIX OR STDIO
mcp__dftracer__view(directory=SPLIT, query='cat == "POSIX" OR cat == "STDIO"')

# Slow events only (> 1 ms)
mcp__dftracer__view(directory=SPLIT, query='cat == "POSIX" and dur > 1000')

# C_APP spans annotated as I/O
mcp__dftracer__view(directory=SPLIT, query='cat == "C_APP" and args.comp == "io"')

# Everything except dftracer metadata
mcp__dftracer__view(directory=SPLIT, query='cat != "dftracer"', no_metadata=True)
```

### Presets

```python
mcp__dftracer__view(directory=SPLIT, preset="io")       # STDIO-level I/O events
mcp__dftracer__view(directory=SPLIT, preset="compute")  # C_APP compute spans
mcp__dftracer__view(directory=SPLIT, preset="dlio")     # deep-learning I/O
```

### Time and duration filters

```python
# Events in first 500 ms (time in microseconds)
mcp__dftracer__view(directory=SPLIT, time_range="0,500000")

# Events longer than 10 ms
mcp__dftracer__view(directory=SPLIT, min_duration=10000)

# Events shorter than 1 ms
mcp__dftracer__view(directory=SPLIT, max_duration=1000)
```

### Output control

```python
# Default: no_metadata=True — strips ph=M hash-mapping events, returns only span events
mcp__dftracer__view(directory=SPLIT, query='cat == "POSIX"')

# Include FH (file-hash→path) events for filename resolution
mcp__dftracer__view(directory=SPLIT, query='cat == "POSIX"', no_metadata=False)

# Save to file instead of stdout
mcp__dftracer__view(directory=SPLIT, query='cat == "POSIX"', output_file="/tmp/posix.ndjson")

# Stream events as they match (good for large traces)
mcp__dftracer__view(directory=SPLIT, query='cat == "POSIX"', stream=True)
```

### Output format

Returns NDJSON — one JSON object per line:
```json
{"id":45,"name":"open","cat":"POSIX","pid":3112,"tid":3117,"ts":1781809100984435,"dur":3,"ph":"X","args":{"hhash":"96567aa8c9616994","flags":2,"fhash":"ba42359754857e43"}}
```

The summary line (`View: custom | Files: 1 | Chunks: scanned=1 skipped=0 | Events: matched=3 scanned=1190`)
goes to stderr and is not returned.

---

## Full tool mapping

| Task | USE THIS tool | Never do this |
|------|--------------|---------------|
| **Read / query events** | `mcp__dftracer__view` | `gzip.open` + `json.loads` loop |
| **Count events by category** | `mcp__dftracer__event_count` | `grep -c` or gzip+json loop |
| **Compare two runs** | `mcp__dftracer__comparator` | pandas scripts |
| **Summarise I/O stats** | `mcp__dftracer__info` | `dftracer_info` via bash |
| **Per-function / per-file stats** | `mcp__dftracer__stats` | manual aggregation |
| **Diagnose bottlenecks** | `mcp__dftracer__diagnose` | reading parquet directly |
| **Aggregate across files** | `mcp__dftracer__aggregator` | looping over .pfw.gz |
| **Show call tree** | `mcp__dftracer__call_tree` | reconstructing spans manually |
| **Split raw traces** | `mcp__dftracer__split` | `dftracer_split` CLI directly |
| **Merge trace directories** | `mcp__dftracer__merge` | `cp` + manual rename |
| **Build index** | `mcp__dftracer__index` | skipping and scanning raw |
| **Plot timeline** | `mcp__dftracer__plot` | matplotlib scripts |

---

## comparator — key patterns

```python
# Always use group_by_dims as a SINGLE comma-separated string
mcp__dftracer__comparator(
    baseline=PREV_SPLIT,
    variant=CUR_SPLIT,
    query='cat == "POSIX" OR cat == "STDIO" OR cat == "C_APP"',
    group_by_dims="cat,name",   # single string — NOT multiple args
    output_format="table",      # "table" for display, "json" for programmatic
    threshold_pct=5.0,
)
```

Significance: `~`=NEGLIGIBLE · `*`=SMALL · `**`=MEDIUM · `***`=LARGE (Cohen's d).
Single-run comparisons always show `~` — run multiple reps for statistical power.

The `session_optimization_iteration` tool automatically compares `opt{N-1}/traces_split`
vs `opt{N}/traces_split` and saves the result to `opt{N}/comparison.json` — no manual
comparator call needed inside the optimization loop.

---

## Known bugs / fixes

| Bug | Fix applied |
|-----|-------------|
| `--group-by` duplicate flags | `dftracer_utils_service.py` now passes `--group-by "cat,name"` as single arg |
| `no_metadata` parameter ignored in `view` | Fixed — `no_metadata=True` is now the default and is correctly forwarded |
| Direct gzip parsing shows `?` for filenames | Use `mcp__dftracer__view(no_metadata=False)` to get FH events with filename resolution |
| `dfanalyzer`'s internal "Trace Count" differs from `event_count`/`dftracer_info`'s "Valid Events" | Not a data-quality bug — two different counting conventions (e.g. 71.8M vs 26.2M on the same trace set, 1000genome-workflow session 2026-07-25). Treat `event_count`/`dftracer_info`'s "Valid Events" as the ground-truth count for trace-quality sanity checks, not dfanalyzer's own "Trace Count". |
| **`comparator` silently returns 0 matched events on Python FUNCTION-mode traces** (confirmed, AF3 session 2026-08-02) | **NOT YET FIXED — this is an upstream `dftracer_comparator` CLI parser gap, not a bug in this repo's MCP wrapper** (the wrapper is a thin `subprocess` call to the compiled `dftracer_comparator` binary from the `dftracer-utils` package — see `dftracer_utils_service.py`'s `comparator()`). Root cause: AF3's Python-side `@_dft.log`/`DFTracerFn` events encode the Chrome-Trace `ph` (phase) field as an INTEGER, while `dftracer_comparator`'s parser expects the standard Chrome-Trace STRING codes (`"X"`, `"B"`/`"E"`, etc.). No error is raised — every query just reports 0 unique keys / 0 total events for both baseline and variant, which looks exactly like "no measurable difference" if you don't independently sanity-check the event count first. **Always verify `event_count` on both trace sets is non-zero AND roughly matches `comparator`'s own reported totals before trusting a `comparator` result — if `comparator` reports 0 but `event_count` doesn't, the traces are almost certainly Python/FUNCTION-mode with integer `ph`, and comparator's numbers are not trustworthy for that pair.** Workaround until the upstream parser is fixed: aggregate per-`(cat,name)` durations manually — `zcat <trace>.pfw.gz \| python3 -c "...json.loads per line, sum 'dur' grouped by ('cat','name')..."` — this is exactly how the AF3 baseline analysis (1eby vs 1e66) was actually compared. This affects ANY app traced via Python `dft_fn`/`DFTracerFn` (not AF3-specific) — C/C++ `DFTRACER_*_FUNCTION_START/END` macro-based traces were not confirmed to have the same issue and may already emit string `ph` codes; check before assuming the bug applies there too.|
| **`analyze()` can exhaust `ulimit -u` (max user processes/threads) on a SMALL trace with a large `cluster_n_workers`** (confirmed, RAJAPerf session 2026-08-05: 11MB/1.2M-event trace, `cluster_n_workers=32`) | `DFTUtilsError: Resource temporarily unavailable` — each dask worker spawns ~192 OS threads internally, so `cluster_n_workers=32` alone requests ~6144 threads regardless of how small the trace is. This directly conflicts with the existing "always use `cluster_n_workers=32`, never `cluster_cores`" guidance from prior large-trace sessions (h5bench, IOR) — that default is right-sized for LARGE traces where the parallelism matters and the workers/nodes have headroom, but is oversized for a small trace on a single node. **Scale `cluster_n_workers` to trace size, don't use a single blanket constant**: for a trace under roughly a few tens of MB / low millions of events, try a small worker count first (4-8, or even the `cluster_n_workers=1` already recommended for small-file-count IOR traces in the `workload-ior` skill — that entry's race-condition concern is a SEPARATE reason to prefer 1, reinforcing the same "don't default to 32" conclusion for small cases). Reserve `cluster_n_workers=32` for genuinely large multi-hundred-MB+ trace sets. Not yet fixed at the tool level (would need a trace-size-aware default in `dfanalyzer_service.py` rather than a hardcoded caller convention) — workaround is manual `gzip`+`json` aggregation, same as the other `analyze()`/`comparator()` fallback rows in this table.|

---

## Fallback rule

Only use bash/CLI to process traces if:
1. The required MCP tool is explicitly unavailable (server down, tool missing), AND
2. You inform the user of the fallback

Even then, prefer `dftracer_view` / `dftracer_comparator` CLI binaries over raw
`gzip.open` + `json.loads` parsing.

---

## ALWAYS split before analyzing (2026-07-08, measured)

`dfanalyzer` / `mcp__dftracer__analyze` **silently truncates a directory of raw
per-rank `*.pfw.gz` files** — it reported `trace_event_count=1542` and
`unique_process_count=1` on traces that actually held ~99,666 events across 8
ranks (identical result whether pointed at the directory or at the single
largest rank file). The numbers look plausible, so this fails silently.

**Fix — run `dftracer_split` first, then analyze the SPLIT output:**

```bash
dftracer_split -d <raw_dir> --output <split_dir> \
  --index-dir <split_dir>/idx --compress --app-name <run>
dfanalyzer trace_path=<split_dir> analyzer/preset=posix cluster.n_workers=32
```

After splitting, the same traces analyze correctly (98,695 events / 8 processes
/ 2 nodes). Verify `Total Processes` matches your rank count — if it shows 1
process / ~1542 events you are still pointing at raw traces.
(`mcp__dftracer__analyze` has since been patched to auto-split raw dirs.)

## `dftracer_view` query DSL gotcha

Compound queries with parentheses / `or` **silently match zero events** instead
of erroring:

```bash
# WRONG — returns nothing, no error
dftracer_view -d <dir> --query 'cat == "POSIX" and (name == "write" or name == "pwrite")'

# RIGHT — filter on one predicate, narrow downstream
dftracer_view -d <dir> --query 'cat == "POSIX"' --stream --no-metadata
```

Always sanity-check that a query returns a non-zero event count before trusting
an "empty" result.

## Measure write BYTES per rank, not write CALLS

To detect a serialized / single-writer I/O pattern, aggregate `args.ret` (bytes
actually written) per `pid` over POSIX `write`/`pwrite` events. Counting
*distinct pids that issue a write* is misleading — every rank writes a few bytes
to log files, so a fully serialized run still shows all N ranks "writing"
(observed: 384/384 pids issued writes while ONE rank held 91% of the bytes).
Compare the top-1 rank's share of total bytes and the number of ranks writing
>10 MB. See [[workload-flashx]] for the Flash-X serial-HDF5 case.


---

## Context economy: query the graph, don't read the tree

Before any step that would open source files, use the `graphify` knowledge graph
(project dependency `graphifyy`, CLI `graphify`):

```bash
graphify query "<target>" --budget 1200   # locate: NODE <sym> [src=file loc=Lnn]
graphify explain <symbol>                 # definition + callers/callees
graphify affected <symbol> --depth 2      # blast radius before you change it
graphify update .                         # refresh after edits (~4s, no LLM)
```

Measured on this repo: locating cost 986 tokens vs 29,456 to read the three
relevant files (3.3%). Run `affected` before editing any shared function and
state the blast radius. Use the CLI, never `graphify-mcp` — its extra tool
schemas would sit in context permanently. See [[dftracer-context-economy]].

## MCP tool fixes (2026-07-09) — what changed and what to still watch

1. **`session_annotation_report` no longer reports 0/N.** It now resolves the tracer alias
   via AST: `_dft = dft_fn("x")` + `@_dft.log` is the normal idiom, and the old regex only
   matched literal `@dft_fn` / `@dft_ai`, so real coverage read as zero. It falls back to
   the regex scanner only when the file does not parse.

2. **`analyze()` non-determinism fixed.** The auto-split output used to be written to
   `<input>/.dfa_split`, i.e. *inside* the directory being split, so `dftracer_split -d
   <input>` could ingest its own partially-written output. Identical calls returned
   265,294 events / 13 processes vs 606,846 / 37 on a trace with a known 925,828 / 64.
   Output now goes to a sibling `.dfa_split_<name>`. Delete any stale nested `.dfa_split`.

3. **`session_analyze_traces(query_type=...)` now validates.** `dftracer_info --query`
   accepts ONLY `summary` or `detailed`; anything else was silently ignored and returned
   the summary. Invalid values now error instead of pretending to work.

4. **`session_generate_optimization_proposals` accepts an external diagnosis.** It no longer
   dead-ends with "No optimization iterations found" for runs launched outside
   `session_optimization_iteration` (e.g. a two-phase `flux batch` job). Pass
   `bottlenecks_json=...` or write `<ws>/<run>/analysis/diagnosis.json`.

### Still true regardless of tooling
- Cross-check any `analyze()` summary against `event_count` and the known pid count.
- An empty `diagnose()` is a tool signal, not "no bottlenecks".
- Rank bottlenecks by aggregated `dur`, never by event count (STDIO: 166,952 events, 1.5s).

## Two confirmed tool bugs found during a YGM/ygm-bench session (2026-08-04)

1. **`mcp__dftracer__reader` (`mode="lines"`) rejects `start=0`.** The default/
   zero start value fails with `Line numbers must be 1-based (start from 1)`
   from the underlying `dftracer_reader` CLI — the MCP tool's own default
   doesn't match the CLI's 1-based requirement. Always pass `start=1`
   explicitly (or any positive line number) rather than relying on the
   parameter default.

2. **`mcp__dftracer__view` (and any `--query` with an embedded double-quoted
   string, e.g. `cat == "CPP_APP"`) silently loses the quotes** when this
   session's `flux proxy <job> flux run ...` wrapping is in the call path —
   the query DSL parser receives `cat==CPP_APP` (unquoted) instead of
   `cat=="CPP_APP"`, and fails or misparses. Reproduced manually outside the
   MCP tool too, so this is in the flux argv-forwarding path, not something
   `view`/`dftracer_view` itself does wrong. `--preset` (no quoted value)
   passes through fine — isolates the bug to quoted-string queries
   specifically. Until fixed, avoid quoted-string DSL queries through
   `mcp__dftracer__view` on any Cray/flux-proxy system; use `--preset` filters
   or `mcp__dftracer__reader`/`event_count` instead.

Both reported for confirmation, not yet fixed at the tool-code level — see
[[dftracer-annotation-lessons]] LESSONS_LOG.md 2026-08-04 (YGM/ygm-bench
session) for the full context these were found in.

## Trace completeness: check the trailing `end` event, not the file size

A rank that dies during teardown still leaves a trace file on disk, often a large and
perfectly plausible one. Counting files, or even counting zero-byte files, does NOT tell
you whether a trace is complete.

**A complete dftracer app trace ends with an `end` event**, written when the logger
finalizes:

```json
{"cat": "dftracer", "name": "end", ...}
```

So the completeness check is: read the LAST parseable record of every `*-app.pfw.gz` and
require `cat == "dftracer"` and `name == "end"`.

```python
def is_complete(path):
    last = None
    for line in gzip.open(path, 'rt'):
        line = line.strip().rstrip(',')
        if line and line not in '[]':
            last = line
    e = json.loads(last)
    return e.get("cat") == "dftracer" and e.get("name") == "end"
```

Use this as the gate before analysing or compacting a run, and report it as
`N/total end-terminated`. Layer it with the cheaper checks rather than replacing them:

1. file count == expected rank count,
2. zero-byte count == 0,
3. **trailing `end` event on every file** — the only one that proves the logger flushed.

Worked example: a miniFE sweep whose app SIGABRTed at teardown on every run still produced
16/16 non-empty traces per run, and all 96 were `end`-terminated — proving the abort was
strictly post-flush and the data was safe to use. The reverse case (missing `end`) means
the tail is lost even though the file looks healthy.
