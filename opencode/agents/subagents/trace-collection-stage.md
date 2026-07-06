---
description: Run the DFTracer-instrumented application, collect traces, and run an initial analysis.
name: trace-collection-stage
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
    "mpiexec *": allow
    "mpirun *": allow
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

# trace-collection-stage

Inputs expected:
  - run_id
  - workspace
  - smoke_cmd
  - dftracer_init_env (optional, default `{"DFTRACER_INIT": "PRELOAD"}`)
  - app_name (optional; if absent, derived from the first component of RUN_ID)

Steps:
1. Determine APP_NAME: use the provided `app_name` if present, otherwise take the first component of `RUN_ID`.
2. Create trace output subdirectory: `mkdir -p <workspace>/traces/<app_name>`.
3. Run the smoke test under DFTracer in the annotated build directory:
   `session_run_with_dftracer(run_id=RUN_ID, command=SMOKE_CMD, subfolder="build_ann",
    env_extra={**DFTRACER_INIT_ENV, "DFTRACER_ENABLE": "1", "DFTRACER_INC_METADATA": "1"},
    data_dir="all")`
   - If using MPI/OpenMPI as root, add env: `OMPI_ALLOW_RUN_AS_ROOT=1`, `OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1`.
4. Copy `.pfw.gz` files up to the traces root:
   `cp <workspace>/traces/<app_name>/*.pfw.gz <workspace>/traces/ 2>/dev/null || true`
5. Split traces by app: `session_split_traces(run_id=RUN_ID, app_name=APP_NAME)`.
6. Analyze traces: `session_analyze_traces(run_id=RUN_ID, query_type="summary")`.
7. Record runtime, exit code, and any relevant stdout/stderr excerpt.

Return ONLY valid JSON matching this schema:

```json
{
  "stage": "trace-collection",
  "summary": "DFTracer trace collection and initial analysis completed",
  "commands": [
    "mkdir -p <workspace>/traces/<app_name>",
    "session_run_with_dftracer(run_id=RUN_ID, command=SMOKE_CMD, subfolder=\"build_ann\", env_extra={**DFTRACER_INIT_ENV, \"DFTRACER_ENABLE\": \"1\", \"DFTRACER_INC_METADATA\": \"1\"}, data_dir=\"all\")",
    "cp <workspace>/traces/<app_name>/*.pfw.gz <workspace>/traces/ 2>/dev/null || true",
    "session_split_traces(run_id=RUN_ID, app_name=APP_NAME)",
    "session_analyze_traces(run_id=RUN_ID, query_type=\"summary\")"
  ],
  "notes": ["MPI env notes", "trace count/size notes", "analysis summary notes"],
  "handoff": {
    "run_id": "<RUN_ID>",
    "app_name": "<app_name>",
    "trace_paths": ["<workspace>/traces/<app_name>/*.pfw.gz"],
    "dftracer_init_env": {"DFTRACER_INIT": "PRELOAD"},
    "analysis_summary": "<brief summary from session_analyze_traces>"
  }
}
```

If the trace collection or analysis fails, return JSON with `error` and `exit_code` fields.
