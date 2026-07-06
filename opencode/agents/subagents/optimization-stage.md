---
description: Run the iterative DFTracer optimization pipeline (L1/L2/L3) with paper-backed proposals.
name: optimization-stage
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

# optimization-stage

Inputs expected:
  - run_id
  - workspace
  - app_name
  - smoke_cmd
  - dftracer_init_env (optional, default `{"DFTRACER_INIT": "PRELOAD"}`)
  - trace_paths (optional list of glob patterns)

Steps:

1. Ask the user: "Generate optimization proposals? [yes / no]"
   - If "no", return the JSON below with `summary` noting optimization was skipped and `notes` containing the user answer.

2. Baseline iteration (no optimizations applied yet):
   `session_optimization_iteration(run_id=RUN_ID, command=SMOKE_CMD, app_name=APP_NAME, data_dir="all", env_extra=DFTRACER_INIT_ENV, optimization_applied="baseline", rebuild=False)`
   - Record baseline raw write rate and completion time.

3. Generate initial proposals:
   `session_generate_optimization_proposals(run_id=RUN_ID, iteration=-1)`
   - Score each proposal using the paper relevance rubric:
     - Bottleneck match (0-50): how directly the paper addresses the bottleneck seen in the traces.
     - System match (0-30): how applicable the paper's setup is to the current app, filesystem, and MPI/HDF5 configuration.
     - Recency (0-20): weight more recent papers higher; seminal papers still get a base score.
   - Keep the top-scoring proposals and discard the rest.

4. Iterative optimization loop (max 10 iterations):
   For i = 1..10:
     a. `session_optimize_l1_app(run_id=RUN_ID)`
     b. `session_optimize_l2_software(run_id=RUN_ID)`
     c. `session_optimize_l3_filesystem(run_id=RUN_ID)`
     d. `session_optimization_iteration(run_id=RUN_ID, command=SMOKE_CMD, app_name=APP_NAME, data_dir="all", env_extra=DFTRACER_INIT_ENV, optimization_applied="iter-<i>: L1+L2+L3", rebuild=True)`
     e. `comparator(trace_a=TRACE_ITER_<i-1>, trace_b=TRACE_ITER_<i>)`
     f. `session_generate_optimization_proposals(run_id=RUN_ID, iteration=i)`
     g. Update a loop state table with columns: iteration, L1 change, L2 change, L3 change, raw write rate, completion time, comparator delta, proposal count, status.

   Stop early if any of these termination conditions are met:
     - EXHAUSTED: no new proposals are generated for an iteration.
     - CONVERGED: delta vs previous iteration is below the convergence threshold for both raw write rate and completion time.
     - REGRESSED: raw write rate drops by more than 5% or completion time worsens by more than 5% vs the best iteration so far.
     - MAX_ITERS: i reaches 10.

5. Final all-layers summary:
   - Print the completed loop state table.
   - Identify the iteration with the best raw write rate as the "recommended configuration".
   - Summarize the cumulative L1/L2/L3 settings applied at the recommended iteration.
   - Note any iterations that were skipped or rolled back.

Return ONLY valid JSON matching this schema:

```json
{
  "stage": "optimization",
  "summary": "Optimization loop completed: <N> iterations, recommended iter <K>",
  "commands": [
    "session_optimization_iteration(... optimization_applied=\"baseline\", rebuild=False)",
    "session_generate_optimization_proposals(run_id=RUN_ID, iteration=-1)",
    "session_optimize_l1_app(run_id=RUN_ID)",
    "session_optimize_l2_software(run_id=RUN_ID)",
    "session_optimize_l3_filesystem(run_id=RUN_ID)",
    "session_optimization_iteration(... optimization_applied=\"iter-<i>: L1+L2+L3\", rebuild=True)",
    "comparator(trace_a=TRACE_ITER_<i-1>, trace_b=TRACE_ITER_<i>)",
    "session_generate_optimization_proposals(run_id=RUN_ID, iteration=i)"
  ],
  "notes": [
    "Termination reason: EXHAUSTED|CONVERGED|REGRESSED|MAX_ITERS",
    "Baseline raw write rate: ...",
    "Best raw write rate: ... at iteration ..."
  ],
  "handoff": {
    "run_id": "<RUN_ID>",
    "iterations": [
      {
        "iteration": -1,
        "optimization_applied": "baseline",
        "raw_write_rate": 0.0,
        "completion_time": 0.0,
        "status": "baseline"
      }
    ],
    "recommended_iter": 0,
    "recommended_config_summary": "<summary of L1/L2/L3 at recommended iter>",
    "termination_reason": "EXHAUSTED|CONVERGED|REGRESSED|MAX_ITERS",
    "proposals": ["<list of final proposal identifiers/summaries>"]
  }
}
```

If the optimization loop fails, return JSON with `error`, `failed_step`, and the last loop state.
