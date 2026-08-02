---
name: feedback-lustre-io
description: "App data (datasets, fractals, checkpoints) → Lustre; dftracer traces → <ws>/<run_name>/traces/raw/ (workspace, NOT Lustre)"
metadata: 
  node_type: memory
  type: feedback
---

**Canonical home:** codified in CLAUDE.md Pipeline Policy rule 11, and see
`workload-scaffold` (the Lustre-vs-workspace pattern applied end-to-end).

# I/O placement for AI/ML workloads on Tuolumne

**App data** (fractals, datasets, benchmark runs, checkpoints) must target Lustre (`/p/lustre5/$USER/...`), not NFS.

**Why:** NFS is too slow for parallel dataset generation and distributed training I/O.

**dftracer traces** go in the session workspace under the run-name subdirectory, NOT Lustre:
```
DFTRACER_LOG_FILE=<ws>/<run_name>/traces/raw/<run_name>
```

**Why traces go to workspace:** The MCP pipeline tools (session_analyze_traces, session_optimization_iteration) read traces from the workspace. Putting traces on Lustre breaks the diagnostic loop.

## Run directory structure

Each profiling iteration (baseline, opt1, opt2, …) lives under its own sub-directory:

```
workspaces/<app>/<session>/
  baseline/
    traces/raw/          ← DFTRACER_LOG_FILE prefix here
    traces/compact/      ← session_split_traces output
    scripts/             ← launch scripts for this run
  opt1/
    traces/raw/
    traces/compact/
    scripts/
```

## MCP tools

- `session_init_run(run_id, run_name)` — create the above structure, returns paths
- `session_get_run_paths(run_id, run_name)` — query paths without creating dirs
- `session_list_runs(run_id)` — list all named runs in the workspace
- `session_run_with_dftracer(..., run_name="baseline")` — run with traces to raw/
- `session_split_traces(..., run_name="baseline")` — compact raw/ → compact/
- `session_analyze_traces(..., run_name="baseline")` — analyze compact/

## How to apply

```bash
WS=$PROJECT_ROOT/workspaces/<app>/<session>
LUSTRE=/p/lustre5/$USER/workspaces/<app>/<session>
# App data → Lustre:
torchrun-hpc ... --fract-base-dir $LUSTRE/fractals --base-run-dir $LUSTRE/runs
# Traces → workspace run dir:
export DFTRACER_LOG_FILE=$WS/baseline/traces/raw/baseline
```

See [[feedback-flux-proxy-wrapper]].
