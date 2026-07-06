---
description: Configure, build, and install the original application and DFTracer baseline.
name: build-setup-stage
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

# build-setup-stage

Inputs expected:
  - run_id
  - app_url
  - ref
  - extra_flags
  - smoke_cmd
  - workspace

Steps:
1. If detect-stage reported HDF5 incompatible, build HDF5 1.14.5 from source into `<workspace>/hdf5_1.14/` (refer to dftracer-install skill) and set HDF5_DIR / LD_LIBRARY_PATH for all subsequent commands. Append `-DHDF5_DIR=<workspace>/hdf5_1.14` to extra_flags.
2. Configure original build: `session_configure(run_id=RUN_ID, extra_cmake_flags=EXTRA_FLAGS)`.
3. Build + install original: `session_build_install(run_id=RUN_ID)`.
4. Run a baseline smoke test of the original (un-annotated) build:
   `session_run_smoke_test(run_id=RUN_ID, command=SMOKE_CMD, subfolder="build")`.
   - Record whether it passed; this is the baseline behavior before annotation.
5. Install dftracer into session: `session_install_dftracer(run_id=RUN_ID)`. On failure, print the cmake/pip error and stop.
6. Copy source to annotated/: `session_copy_annotated(run_id=RUN_ID)`.
7. Baseline annotated build (no macros yet): `session_build_annotated(run_id=RUN_ID, extra_cmake_flags=EXTRA_FLAGS)`. On failure, show cmake/make errors and stop.

Return ONLY valid JSON matching this schema:

```json
{
  "stage": "build-setup",
  "summary": "Baseline build and dftracer install completed",
  "commands": ["session_configure(...)", "session_build_install(...)", "session_run_smoke_test(...)", "session_install_dftracer(...)", "session_copy_annotated(...)", "session_build_annotated(...)"],
  "notes": ["any HDF5/MPI notes", "baseline smoke passed/failed"],
  "handoff": {
    "run_id": "<RUN_ID>",
    "workspace": "<workspace>",
    "extra_flags": "<final extra_flags>",
    "smoke_cmd": "<smoke_cmd or auto-detect note>",
    "dftracer_installed": true,
    "baseline_build_passed": true,
    "baseline_smoke_passed": true
  }
}
```

If any step fails, return JSON with `error` and `failed_step` fields.
