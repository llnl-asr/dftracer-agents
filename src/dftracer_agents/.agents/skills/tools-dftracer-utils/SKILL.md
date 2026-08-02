---
name: tools-dftracer-utils
description: dftracer-utils — the Python binding package for the trace-utils CLI (split/merge/compact/reader/event_count on .pfw/.pfw.gz traces). Install verification and its relationship to the dftracer/pydftracer package trio. Load this skill before any trace post-processing step (session_split_traces, reader, event_count, comparator).
metadata:
  type: software
---

## What it provides

`dftracer-utils` is the PyPI package (`pip show dftracer-utils` →
`Summary: Python binding for dftracer-utils`) backing the trace-processing MCP
tools this project uses for ALL trace work — `split`, `merge`, `reader`,
`event_count`, `comparator`, `aggregator`, `stats`, `reconstruct`, `replay`,
`organize`, `pgzip`, `tar` (see the `dftracer-trace-utils` skill's "always use
the MCP tools, never raw gzip/python scripts" rule). It is a dependency of
`dftracer` itself (`Required-by: dftracer, dftracer-agents, dftracer-analyzer`
on the framework's own venv) — installing `dftracer` normally installs this
transitively.

## Verification

```bash
python -c "import dftracer_utils"
```
or check the MCP tool wrapper directly succeeds on a real trace file rather
than trusting `pip show` alone.

## Relationship to dftracer / pydftracer

These are three separate PyPI packages that together make up "the dftracer
ecosystem" for a session:
- **dftracer** — C core, tracing runtime, CMake build. See [[tools-dftracer]].
- **pydftracer** — Python annotation/decorator API (`dftracer.python`). See [[tools-pydftracer]].
- **dftracer-utils** (this skill) — trace post-processing bindings (split/merge/read).

A session can have a working C core and trace-utils (so `session_split_traces`/
`event_count`/`reader` all work fine) while STILL missing `pydftracer` — these
are independent failure modes; verifying one does not verify the others. Check
all three explicitly when diagnosing a "dftracer partially works" report.

## Session-local vs shared venv

Same rule as [[tools-dftracer]]: never repair the shared framework venv's
`dftracer-utils` in place as a side effect of a session — install a
session-local `dftracer` (which pulls this in transitively) instead.

## KNOWN BUG (2026-08-02): `stats --report categories/summary` always fails on compacted (split) traces

`dftracer_index --rebuild-summaries` (and the `mcp__dftracer__index` wrapper)
reports `"Events processed: 0"` even against multi-hundred-thousand-event
`.pfw.gz` chunks — it builds the bloom-filter existence index (file
resolution/caching works: `dftracer_stats` reports `N total, N cached, 0
failed`) but never actually scans event content to populate per-chunk
statistics. Every subsequent `dftracer_stats --report categories` (or
`summary`, `detailed`, etc.) call then fails per-file with `"No chunk
statistics in index for <file>"` — reproduced both via the
`mcp__dftracer__stats` MCP tool and the raw `dftracer_stats`/`dftracer_index`
CLI binaries directly, with default AND explicitly-matched `--checkpoint-size`
values, so it is not a chunk-size/checkpoint-size mismatch. `event_count`
(`mcp__dftracer__event_count` / `dftracer_event_count`) is unaffected — it
reports a correct total independently of this index path.

**Root cause NOT yet fixed**: no local source checkout of `dftracer-utils`
exists in this project (it's a pip-installed prebuilt binary), so this needs
fixing upstream in the `dftracer-utils` repo's index-building code (the
`--rebuild-summaries` flag needs to actually walk decompressed event content,
not just build bloom filters) — this project can only work around it, not
patch it, until an editable/source install is available.

**Workaround until fixed**: for a category/event-count breakdown, decompress
and parse directly instead of trusting `stats --report categories/summary`:
```python
import gzip, json, collections
cats = collections.Counter()
for line in gzip.open(chunk_path, "rt"):
    line = line.strip().rstrip(",")
    if not line or line in ("[", "]"):
        continue
    try:
        cats[json.loads(line).get("cat")] += 1
    except Exception:
        pass
```
Run across all chunks in the compact dir (not just a sample — category
distribution is very uneven, e.g. `PP` events from a torch-profiler bridge
can be <0.1% of total events and easy to miss with partial sampling).
