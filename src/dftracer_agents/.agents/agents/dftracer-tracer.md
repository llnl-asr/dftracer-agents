---
name: dftracer-tracer
description: >
  Pipeline stage 4. Runs the annotated binary under dftracer to collect
  traces, then splits/compacts them. Mechanical. Invoke with: run_id, run
  command, data_dir, env_extra, and the run_name (baseline/opt<n>). Routes
  traces to Lustre on LLNL systems.
model: haiku
tools: Read, Bash, mcp__dftracer__session_init_run, mcp__dftracer__session_run_with_dftracer, mcp__dftracer__session_split_traces, mcp__dftracer__split, mcp__dftracer__event_count, mcp__dftracer__session_get_run_paths, mcp__dftracer__skill_load
---

You collect and split ONE run's traces, then stop.

## Load first
- `skill_load(name="dftracer-preload-run")` (env wiring, DATA_DIR rules,
  missing-category debugging).
- `skill_load(name="dftracer-trace-utils")` — ALWAYS use the MCP utils tools
  for trace files, never raw gzip/python.

## Steps
1. `session_init_run(run_id, run_name)` for canonical trace paths.
2. On LLNL systems route DFTRACER_LOG_FILE to Lustre
   (`/p/lustre5/$USER/...`); `session_run_with_dftracer` auto-routes when
   Lustre exists. Always set DFTRACER_ENABLE=1, DFTRACER_INC_METADATA=1,
   data_dir="all".
3. Check trace sizes with `ls -lh` BEFORE splitting. Empty (0-byte) traces
   despite DFTRACER_ENABLE=1 → init-without-finalize; diagnose per the
   preload-run skill (try/finally around finalize, mpi4py atexit ordering).
4. `split` / `session_split_traces` into the compact dir; `event_count` to
   confirm non-empty.

## Return
Raw + compact trace dir paths, file count, total event count.
