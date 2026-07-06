---
description: Build annotated source and run the DFTracer smoke test.
name: build-with-dftracer-stage
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
    "cmake *": allow
    "make *": allow
    "ninja *": allow
    "cc *": allow
    "gcc *": allow
    "g++ *": allow
    "mpicc *": allow
    "mpicxx *": allow
    "h5cc *": allow
    "h5pcc *": allow
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

# build-with-dftracer-stage

Inputs expected:
  - run_id
  - workspace
  - smoke_cmd
  - extra_flags

Steps:
1. Detect explicit INIT/FINI usage in annotated source:
   ```bash
   INIT_COUNT=$(grep -r "DFTRACER_C_INIT\|DFTRACER_CPP_INIT\|DFTracer.initialize_log" <workspace>/annotated/ 2>/dev/null | wc -l)
   FINI_COUNT=$(grep -r "DFTRACER_C_FINI\|DFTRACER_CPP_FINI\|dftracer.finalize_log" <workspace>/annotated/ 2>/dev/null | wc -l)
   ```
   Determine DFTRACER_INIT_ENV:
   - INIT_COUNT > 0 AND FINI_COUNT > 0 → {"DFTRACER_INIT": "HYBRID"}
   - INIT_COUNT > 0 AND FINI_COUNT == 0 → {"DFTRACER_INIT": "PRELOAD"}
   - INIT_COUNT == 0 → {"DFTRACER_INIT": "PRELOAD"}
   Never set DFTRACER_INIT=0.
2. Build annotated version: `session_build_annotated(run_id=RUN_ID, extra_cmake_flags=EXTRA_FLAGS)`.
   - On failure: extract failing function(s), re-annotate only those files with clang_annotate_file excluding the failing function, re-run syntax check + lint, retry. Max 2 retries.
3. Run smoke test: `session_run_smoke_test(run_id=RUN_ID, command=SMOKE_CMD, subfolder="build_ann")`.
   - Add MPI root env if needed: `OMPI_ALLOW_RUN_AS_ROOT=1`, `OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1`.
   - On failure with DFTRACER symbols in error → re-annotate + retry.
   - Otherwise record failure and ask upstream.

Return ONLY valid JSON matching this schema:

```json
{
  "stage": "build-with-dftracer",
  "summary": "Annotated build and smoke test completed",
  "dftracer_init_env": {"DFTRACER_INIT": "HYBRID"},
  "commands": ["session_build_annotated(...)", "session_run_smoke_test(...)"],
  "notes": ["INIT_COUNT=N", "FINI_COUNT=M", "smoke passed"],
  "handoff": {
    "run_id": "<RUN_ID>",
    "dftracer_init_env": {"DFTRACER_INIT": "HYBRID"},
    "build_passed": true,
    "smoke_passed": true,
    "smoke_cmd": "<smoke_cmd>"
  }
}
```

If build or smoke test ultimately fails, return JSON with `error`, `failed_step`, and relevant log excerpt.
