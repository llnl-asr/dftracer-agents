---
description: Post-process DFTracer trace files (compact, index, and prepare for analysis).
name: postprocess-stage
mode: subagent
model: ollama/kimi-k2.7-code:cloud
temperature: 0.1
permission:
  read: allow
  edit:
    "*": ask
  bash:
    "uvx *": allow
    "python *": allow
    "mkdir *": allow
    "cp *": allow
    "mv *": allow
    "ls *": allow
    "cat *": allow
    "rg *": allow
    "grep *": allow
    "dftracer-convert *": allow
    "dftracer-*": allow
    "*": ask
  task: allow
  glob: allow
  grep: allow
  list: allow
  skill: allow
  todowrite: allow
  external_directory:
    "workspaces/**": allow
    "*": ask
---

# postprocess-stage

Inputs expected:
  - run_id
  - workspace
  - app_name
  - trace_paths (optional list of glob patterns)

Steps:
1. Ensure trace directories exist: `mkdir -p <workspace>/traces <workspace>/traces_split`.
2. Copy trace files into canonical location if needed:
   `cp <workspace>/traces/<app_name>/*.pfw.gz <workspace>/traces/ 2>/dev/null || true`
3. Split traces by app: `session_split_traces(run_id=RUN_ID, app_name=APP_NAME)`.
4. If available, compact traces for downstream analysis. Compacted directory: `<workspace>/postprocess/compacted`. Index directory: `<workspace>/postprocess/index`.
5. Run a quick summary analysis: `session_analyze_traces(run_id=RUN_ID, query_type="summary")`.

Return ONLY valid JSON matching this schema:

```json
{
  "stage": "postprocess",
  "summary": "Trace postprocessing completed",
  "commands": ["cp ...", "session_split_traces(...)", "session_analyze_traces(...)"],
  "notes": ["N trace files", "postprocess dir created"],
  "handoff": {
    "run_id": "<RUN_ID>",
    "postprocess_dir": "<workspace>/postprocess",
    "compacted_dir": "<workspace>/postprocess/compacted",
    "index_dir": "<workspace>/postprocess/index",
    "trace_summary": "<brief summary or key metrics>"
  }
}
```

If postprocessing fails, return JSON with `error` and the failing step.
