---
name: dftracer-smoke-test
description: Smoke test rules for dftracer — single-process only, environment variables, DFTRACER_INIT conflict warning, and trace file paths
---

## Smoke Test Rules

**Always run smoke tests as a single process — never with MPI or any parallelism.**

- `session_run_smoke_test` automatically strips MPI launchers (`mpirun`, `mpiexec`,
  `srun`, `jsrun`, `aprun`, `flux run`) and their flags (`-np`, `-n`, `--ntasks`, etc.)
  from the command before running.
- Never use `-j N` (parallel make) inside a smoke test command.
- Never use `OMP_NUM_THREADS > 1` or `GOMP_SPINCOUNT` in smoke test env vars.
- If the application requires at least one MPI rank to start (e.g., calls
  `MPI_Init` unconditionally), run it with `mpirun -np 1 <binary> <args>` so the
  tool strips it to a single rank — NOT with multiple ranks.
- The smoke test command should exercise the minimal happy path: one input file,
  one iteration, smallest possible data size, no checksum or verification flags.

### Run smoke tests on the system-detected parallel file system (PFS)

**HARD RULE — every smoke test, baseline run, and optimization iteration must write
its data and trace output to the system-detected PFS, never to `/tmp` or the shared
home filesystem.**

- Use the PFS path recorded by `system_detect` / `dftracer-system-detect`.
- On **Tuolumne** the PFS is Lustre at `/p/lustre5/$USER`.
- The MCP `session_run_smoke_test` and `session_run_with_dftracer` tools route trace
  files into the workspace automatically, but the application's **data files**
  (checkpoints, plotfiles, IOR output, datasets) must be directed to the PFS by the
  run command or parameter file.
- If a smoke test cannot write to the PFS (e.g., the path is unavailable in the
  current environment), stop and report the missing PFS before running.

**Example for IOR:**
```
# Good — single process, minimal run, output on Lustre
mkdir -p /p/lustre5/$USER/workspaces/ior
./src/ior -a POSIX -b 1m -t 1m -s 1 -F -C -o /p/lustre5/$USER/workspaces/ior/ior_smoke_test

# Bad — MPI multi-process
mpirun -np 4 ./src/ior -a POSIX -b 1m -t 1m -s 1 -F -C -o /p/lustre5/$USER/workspaces/ior/ior_smoke_test

# Bad — output on /tmp or home filesystem
./src/ior -a POSIX -b 1m -t 1m -s 1 -F -C -o /tmp/ior_smoke_test
```

## Key DFTracer Environment Variables (for session_run_with_dftracer)

`session_run_with_dftracer` sets all of these automatically — **do not pass them
in `env_extra` unless you have a specific override reason**:

| Variable | Value set by the tool | Purpose |
|---|---|---|
| `DFTRACER_ENABLE` | `1` | Activate tracing (required) |
| `DFTRACER_INC_METADATA` | `1` | Include process/thread metadata |
| `DFTRACER_LOG_FILE` | `workspaces/<run_id>/traces/<run_id>` | Trace file prefix |
| `DFTRACER_DATA_DIR` | **`all`** (always) | Which paths to record POSIX/HDF5 I/O for |
| `DFTRACER_INIT` | `FUNCTION` *(see note below)* | Auto-initialise without an explicit API call |

**HARD RULE — always set `DFTRACER_DATA_DIR=all`.** `DFTRACER_DATA_DIR` is a
path *filter*: dftracer only records POSIX/HDF5 events whose file paths fall under
it. Any narrower value silently drops every I/O event outside that dir — including
checkpoints/datasets on Lustre, `/tmp`, or the real cwd — leaving a trace with only
`C_APP` annotation events (which are NOT path-filtered) and no POSIX/HDF5. Set it to
`all` for every run and every smoke test; never scope it to a single dir. FUNCTION
mode with the app linked against dftracer already captures POSIX + HDF5 + MPI via the
built-in brahma interceptors — no PRELOAD needed — but ONLY for paths that pass the
`DATA_DIR` filter, so `all` is what makes those categories appear.

**Also forward it to every rank.** Exporting `DFTRACER_DATA_DIR=all` in a launcher
script is not enough — MPI launchers do not propagate env to compute ranks by
default. With `flux run`, pass each var explicitly (`-x DFTRACER_DATA_DIR -x
DFTRACER_ENABLE -x DFTRACER_INIT -x DFTRACER_LOG_FILE -x LD_LIBRARY_PATH ...`), or
the ranks inherit a stale/leaked value and I/O gets filtered out. Verify after a run:
the trace should contain `FH` (file-hash) metadata entries for the actual data files
(e.g. the Lustre checkpoints), not just the run dir — a single FH pointing at one dir
means `DATA_DIR` was wrong.

**Empty/partial-trace troubleshooting — FUNCTION mode is correct; fix DATA_DIR/LOG_FILE first.**
A 0-byte trace with FUNCTION-compiled annotations is almost always a
`DFTRACER_DATA_DIR` / `DFTRACER_LOG_FILE` misconfiguration, **NOT** a reason to
switch to PRELOAD/HYBRID. Before suspecting the library:
- Set `DFTRACER_DATA_DIR=all` when the app writes I/O anywhere outside the
  monitored source dir (checkpoints, plotfiles, datasets on Lustre, `/tmp`, cwd).
  A narrow `DATA_DIR` silently drops all POSIX/HDF5 events whose paths fall outside
  it, yielding an empty or metadata-only trace even though annotations executed.
- Ensure `DFTRACER_LOG_FILE` is an absolute path into the run's `traces/raw` dir
  **and that dir exists** (create it first — a missing dir logs `unable to create
  log file ... errno=2` and produces a 0-byte file).
- Keep `DFTRACER_INIT=FUNCTION` for annotated binaries. Verified on Flash-X Sedov
  3D at 768 ranks: FUNCTION + `DFTRACER_DATA_DIR=all` + valid LOG_FILE dir →
  ~284k events with HDF5 I/O categories. The earlier empty smoke trace was purely
  the DATA_DIR/LOG_FILE issue above.

**DFTRACER_INIT conflict warning:** If the annotated source already contains explicit
`DFTRACER_C_INIT()` / `DFTRACER_CPP_INIT()` calls (added during Pass 1), do NOT set
`DFTRACER_INIT=1`. Both active simultaneously causes double-init, producing an empty
or corrupted trace file. Pass `env_extra='{"DFTRACER_INIT":"FUNCTION"}'` in that case.

Heuristic: `grep -r "DFTRACER_C_INIT\|DFTRACER_CPP_INIT" annotated/` — if any matches,
set `DFTRACER_INIT=FUNCTION`.

**`DFTRACER_LOG_FILE` must always be an absolute path inside the workspace run directory.**
Trace files land at `workspaces/<run_id>/traces/<run_id>.<pid>.pfw`.

**Never set `DFTRACER_LOG_FILE` to `/tmp/` or any path outside the workspace.**

If running the application manually outside the MCP tool:
```bash
export DFTRACER_ENABLE=1
export DFTRACER_INC_METADATA=1
export DFTRACER_LOG_FILE=/absolute/path/to/workspaces/<run_id>/traces/<run_id>
export DFTRACER_DATA_DIR=all   # use 'all' when I/O lands outside the source dir (checkpoints/datasets on Lustre)
export DFTRACER_INIT=FUNCTION  # FUNCTION for annotated binaries; only use PRELOAD for un-annotated apps
```
