---
description: Detect application environment, HDF5/MPI availability, and initialize the DFTracer session.
name: detect-stage
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
    "h5cc *": allow
    "h5pcc *": allow
    "pkg-config *": allow
    "find *": allow
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

# detect-stage

Inputs expected:
  - app_url: Git URL of the application
  - ref: branch or tag (default "main")
  - extra_flags: extra CMake/configure flags (optional)
  - smoke_cmd: smoke test command (optional)

Steps:
1. Call `session_create(url=app_url, ref=ref)` to create the DFTracer session.
   Capture run_id and workspace path from the response.
2. Detect HDF5 version and compatibility. Preferred first detection call:
   `session_detect(run_id=RUN_ID)`.
   - If `session_detect` is unavailable or does not return HDF5 information, fall back to bash detection:
     `h5cc --version 2>/dev/null || h5pcc --version 2>/dev/null || pkg-config --modversion hdf5 2>/dev/null`
     and check for the H5public.h header: `find /usr -name "H5public.h" 2>/dev/null | head -1`.
   - HDF5 guidance:
     - Required minimum: HDF5 ≥ 1.14.x
     - Preferred/tested patch: 1.14.5
     - If system HDF5 < 1.14.x, build 1.14.5 from source into `<workspace>/hdf5_1.14/` and add `-DHDF5_DIR=<workspace>/hdf5_1.14` to EXTRA_FLAGS.
3. Detect MPI availability: `mpicc --version`, `mpicxx --version`, `mpiexec --version`.
4. Record app_name as the first component of RUN_ID (the repository/project name).

Return ONLY valid JSON matching this schema:

```json
{
  "stage": "detect",
  "summary": "<one-line result>",
  "run_id": "<RUN_ID>",
  "workspace": "<absolute workspace path>",
  "app_name": "<first component of RUN_ID>",
  "hdf5_compatible": true|false,
  "hdf5_action": "system|build_1.14|none",
  "mpi_detected": true|false,
  "commands": ["session_create(...)", "session_detect(...)", "h5cc --version", ...],
  "notes": ["note 1", "note 2"],
  "handoff": {
    "run_id": "<RUN_ID>",
    "workspace": "<absolute workspace path>",
    "app_name": "<app_name>",
    "extra_flags": "<updated extra_flags>",
    "smoke_cmd": "<smoke_cmd or auto-detect note>",
    "mpi_detected": true|false
  }
}
```

If session_create fails, return JSON with `error` field and stop.
