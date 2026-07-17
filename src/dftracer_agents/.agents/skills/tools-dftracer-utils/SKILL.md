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
