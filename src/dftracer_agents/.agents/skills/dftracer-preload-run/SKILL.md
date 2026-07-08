---
name: dftracer-preload-run
description: >
  Reference for running applications with dftracer in PRELOAD or HYBRID mode.
  Covers correct environment variable setup, DATA_DIR rules, LD_PRELOAD wiring,
  MPI forwarding, and trace verification. Use before any session_run_with_dftracer
  call or when debugging missing trace categories (POSIX/HDF5/MPI).
---

# DFTracer PRELOAD and HYBRID Run Reference

Source: https://dftracer.readthedocs.io/en/latest/api.html

---

## Three Modes

| Mode     | When to use                                      | DFTRACER_INIT | Source annotations needed |
|----------|--------------------------------------------------|---------------|--------------------------|
| FUNCTION | App profiling only — no I/O interception        | `FUNCTION`    | DFTRACER_C_INIT + FINI + START/END macros |
| PRELOAD  | Transparent I/O capture without source changes  | `PRELOAD`     | None |
| HYBRID   | Function profiling AND I/O interception together | `HYBRID`      | DFTRACER_C_INIT + DFTRACER_C_FINI (both required) |

**HYBRID rule:** Use `DFTRACER_INIT=HYBRID` only when the annotated source
contains **both** `DFTRACER_C_INIT(...)` and `DFTRACER_C_FINI()`. If only one
is present, use `PRELOAD` instead (missing FINI leaves the trace open).

**Fortran programs rule:** Fortran codes (e.g. Flash-X) have no C `main()`.
For FUNCTION/HYBRID mode, create a C wrapper with `__attribute__((constructor))`
and `__attribute__((destructor))` calling DFTRACER_C_INIT/DFTRACER_C_FINI, compile
it to `.o`, and link it into the binary. If the Fortran linker (e.g. CCE `crayftn`)
does not reliably fire constructors, **use PRELOAD mode instead** — it captures
HDF5/POSIX/MPI I/O at the library level without requiring INIT/FINI in the
application binary. See [[dftracer-annotation-lessons]] PF1 for the wrapper pattern.

---

## Run on the system-detected parallel file system (PFS)

**HARD RULE — every multinode run, baseline trace, and optimization iteration must
write its data files and trace output to the system-detected PFS. Never use `/tmp`,
`/scratch` (unless it is the detected PFS), or the shared home filesystem for I/O
benchmarking.**

- Determine the PFS from the system-detect skill / `system_detect` output.
- Known mappings:
  - **Tuolumne (LLNL Lustre)** → `/p/lustre5/$USER`
- The application's output path (`-o`, `--output-file`, `checkpointFileNumber`,
  plotfile prefix, etc.) must point under the detected PFS.
- `session_run_with_dftracer` routes trace files into the workspace automatically,
  but the **data files** must be directed to the PFS by the run command or
  parameter file.
- If the PFS path is not available, stop and ask the user to confirm the system
  or allocation before running.

## Allocation-Aware Run Rules (MANDATORY for Production Runs)

**Every baseline and optimization iteration must run on the user's active allocation with ALL nodes.**

1. **Ask the user for their active allocation ID** before any large run. If they forgot, prompt them.
2. **Verify the allocation is active** with `flux jobs` — check that the allocation ID shows status `R` (running).
3. **Use ALL nodes in the allocation** with `--exclusive`:
   ```bash
   flux proxy <alloc_id> flux run -N <nnodes> -n <ntasks> --exclusive [other flags] ./app
   ```
4. **Problem size must be large enough**:
   - Use ~50% of total node memory across all nodes
   - Run for at least 30 minutes of wall time
   - Generate multi-GB checkpoint files
5. **Never compare smoke test against production** — baseline and optimization iterations must be the same run class (both production-scale).
6. **Create Lustre output directory before running**:
   ```bash
   mkdir -p /p/lustre5/$USER/<app>/<run_name>
   ```

## Flux Proxy Run Pattern (MANDATORY)

**Never pass environment variables inline with `flux proxy <id> flux run -x VAR`.**
`flux proxy` opens an SSH tunnel to the allocation broker; environment variables
set in the local shell are **not** automatically forwarded through the proxy.
Instead, **wrap the entire run in a bash script** and invoke that script via
`flux proxy`:

```bash
# 1. Create a run script that exports ALL env vars internally
cat > production_run.sh << 'EOF'
#!/bin/bash
set -e

# Module setup
module load PrgEnv-cray/8.7.0
module load cce/20.0.0
module load cray-mpich/9.0.1

# Environment
export PATH="/usr/WS2/haridev/dftracer-agents/.venv/bin:$PATH"
export LD_LIBRARY_PATH="${WS}/hdf5_1.14/lib:${WS}/install/lib/python3.13/site-packages/dftracer/lib64:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:$LD_LIBRARY_PATH"

# DFTracer setup
export DFTRACER_ENABLE=1
export DFTRACER_INIT=PRELOAD
export DFTRACER_DATA_DIR=all
export DFTRACER_INC_METADATA=1
export DFTRACER_LOG_FILE="${WS}/traces/raw/baseline"
export LD_PRELOAD="${WS}/install/lib/python3.13/site-packages/dftracer/lib64/libdftracer_preload.so"

# MPI / HDF5 settings
export MPICH_GPU_SUPPORT_ENABLED=0
export HDF5_USE_FILE_LOCKING=FALSE

# Run the application
cd "${FLASHX_DIR}"
./flashx
EOF
chmod +x production_run.sh

# 2. Submit the script via flux proxy — NO -x flags needed
flux proxy <alloc_id> flux run -N <nnodes> -n <ntasks> --exclusive ./production_run.sh
```

**Why this works:** The bash script runs inside the allocation where it sets its
own environment. `flux proxy` only needs to forward the script execution;
all DFTracer variables are established locally within the script.

**Anti-pattern (DO NOT USE):**
```bash
# WRONG — env vars are lost across the proxy boundary
export DFTRACER_ENABLE=1
flux proxy <id> flux run -N 8 -n 768 --exclusive -x DFTRACER_ENABLE ./flashx
```

**When `-x` IS appropriate:** For single-node runs or direct `flux run` (without
`flux proxy`), `-x` is required to forward env vars to MPI ranks:
```bash
# OK — direct flux run on a single node, no proxy boundary
flux run -N 1 -n 4 -x DFTRACER_ENABLE -x DFTRACER_INIT -x LD_PRELOAD ./flashx
```

## Required Environment Variables

Set ALL of these before invoking the binary (PRELOAD or HYBRID):

```bash
# mandatory
export DFTRACER_ENABLE=1
export DFTRACER_INIT=PRELOAD          # or HYBRID — uppercase, exact string
export DFTRACER_LOG_FILE=/path/to/output/prefix
export DFTRACER_DATA_DIR=/path/to/watch:/another/path

# strongly recommended
export DFTRACER_INC_METADATA=1        # captures rank/file/comp metadata

# required for shared library resolution
export LD_LIBRARY_PATH=<session_venv>/lib/python3.12/site-packages/dftracer/lib:$LD_LIBRARY_PATH

# required to activate I/O interception
export LD_PRELOAD=<session_venv>/lib/python3.12/site-packages/dftracer/lib/libdftracer_preload.so
```

---

## DFTRACER_DATA_DIR Rules

**HARD RULE — always set `DFTRACER_DATA_DIR=all`.** `all` tells dftracer to record
POSIX/HDF5 I/O for every path, no filtering. This is the default for all runs and
smoke tests. `DFTRACER_DATA_DIR` is a path *filter*: any narrower value silently
drops every I/O event whose file path falls outside it — leaving a trace with only
`C_APP` annotation events and no POSIX/HDF5 (a common false alarm). Datasets on
Lustre, `/tmp`, or the real cwd all get filtered out unless `all` is used.

| Goal                               | Value                      |
|------------------------------------|----------------------------|
| Capture all I/O on any file (default) | `DFTRACER_DATA_DIR=all` |
| Capture I/O under /tmp only         | `DFTRACER_DATA_DIR=/tmp`   |
| Capture two specific data dirs      | `DFTRACER_DATA_DIR=/data:/scratch` |

Never leave it empty/unset. If a specific older build rejects `all` with a
`Code 2001` error, use `/` as the equivalent capture-everything value — but `all`
is the standard on current dftracer.

**Forward it to every rank.** Exporting it in a launcher is not enough — MPI
launchers don't propagate env to compute ranks. With `flux run`, pass
`-x DFTRACER_DATA_DIR` (plus `-x` for every other DFTRACER var and `LD_LIBRARY_PATH`).
Omitting `-x DFTRACER_DATA_DIR` leaks a stale value into the ranks and filters out
the real I/O. Verify: the trace's `FH` (file-hash) entries must reference the actual
data files (e.g. Lustre checkpoints), not just the run dir.

---

## MPI Programs: Forwarding Environment Variables

`mpirun` does **not** automatically forward the shell environment to ranks.
You must **export** each variable AND pass it with `-x`:

```bash
# Export all vars first
export DFTRACER_ENABLE=1
export DFTRACER_INIT=PRELOAD
export DFTRACER_DATA_DIR=/tmp
export DFTRACER_LOG_FILE=/tmp/traces/myapp
export DFTRACER_INC_METADATA=1
export LD_LIBRARY_PATH=<dftracer_lib>:$LD_LIBRARY_PATH
export LD_PRELOAD=<dftracer_lib>/libdftracer_preload.so

# Forward every var to each rank with -x
mpirun --allow-run-as-root -np 4 \
  -x DFTRACER_ENABLE \
  -x DFTRACER_INIT \
  -x DFTRACER_DATA_DIR \
  -x DFTRACER_LOG_FILE \
  -x DFTRACER_INC_METADATA \
  -x LD_LIBRARY_PATH \
  -x LD_PRELOAD \
  ./my_app
```

Omitting `-x LD_PRELOAD` means ranks run without interception (no trace files).
Omitting `-x DFTRACER_DATA_DIR` causes Code 2001 on every rank.

---

## Expected Trace Categories

After a successful PRELOAD/HYBRID run, trace files (`<prefix>-<hash>-preload.pfw.gz`)
should contain these Chrome-tracing event categories:

| Category  | Source                          | Requires                    |
|-----------|---------------------------------|-----------------------------|
| `POSIX`   | POSIX open/read/write/close     | `DFTRACER_DATA_DIR` matches |
| `STDIO`   | fopen/fread/fwrite/fclose       | `DFTRACER_DATA_DIR` matches |
| `HDF5`    | H5Fcreate/H5Dwrite etc.         | dftracer built with HDF5    |
| `MPI`     | MPI_File_write/read etc.        | dftracer built with MPI¹    |
| `dftracer`| Init/Fini metadata events       | always present              |

¹ MPI-IO tracing via brahma requires that the dftracer wheel was built with
`DFTRACER_ENABLE_MPI=ON` AND that the runtime MPI shared library matches the
version dftracer was compiled against. Verify with:
```bash
strings libdftracer_core.so | grep "openmpi/include"
# must match the mpi.h path on the system
```

Rank-level metadata (MPI rank, hostname) appears as `PR` (process rank),
`HH` (hostname hash), `FH` (file hash) events in the trace metadata.

---

## Verification Checklist

After running, verify:

```bash
# 1. Trace files exist and are non-empty
ls -lh <DFTRACER_LOG_FILE>*.pfw.gz

# 2. Categories visible in each file
for f in <prefix>*.pfw.gz; do
  zcat "$f" | python3 -c "
import sys, json
cats = set()
for line in sys.stdin:
    l = line.strip().rstrip(',')
    if l in ('[',']') or not l: continue
    try: cats.add(json.loads(l).get('cat',''))
    except: pass
print('$(basename $f):', sorted(cats))
"
done

# 3. MPI rank metadata present
zcat <any>.pfw.gz | python3 -c "
import sys, json
for line in sys.stdin:
    l = line.strip().rstrip(',')
    try:
        e = json.loads(l)
        if e.get('name') == 'rank': print('rank =', e['args']['value'])
    except: pass
"
```

---

## Common Errors and Fixes

| Error                                          | Cause                              | Fix                                      |
|------------------------------------------------|------------------------------------|------------------------------------------|
| `Code 2001: Data dirs not defined`             | `DFTRACER_DATA_DIR` not forwarded or set to `"all"` | Set to a real path, forward with `-x` |
| No trace files written                         | `DFTRACER_INIT` not uppercase or not forwarded | Use `PRELOAD` (uppercase), add `-x` |
| Empty trace files (0 bytes)                    | Library loaded but no I/O in `DATA_DIR` | Expand `DATA_DIR` or use `/`          |
| `HDF5` events missing                          | dftracer not built with `DFTRACER_ENABLE_HDF5=ON` | Reinstall with `HDF5_ROOT` set        |
| `MPI` events missing                           | MPI headers at build time don't match runtime lib | Reinstall with matching OpenMPI       |
| `DFTRACER_INIT=hybrid` → no function events    | String is case-sensitive; lowercase ignored | Use `HYBRID` (uppercase)              |
| Ranks crash with DFTRACER errors               | `LD_PRELOAD` not forwarded to MPI ranks | Add `-x LD_PRELOAD` to mpirun        |

---

## Quick Reference: session_run_with_dftracer

When using the MCP tool, pass these in `env_extra`:

```python
# PRELOAD mode (no source annotations)
session_run_with_dftracer(
    run_id  = RUN_ID,
    command = "mpirun --allow-run-as-root -np 4 -x DFTRACER_ENABLE -x DFTRACER_INIT -x DFTRACER_DATA_DIR -x DFTRACER_LOG_FILE -x DFTRACER_INC_METADATA -x LD_LIBRARY_PATH -x LD_PRELOAD ./myapp",
    data_dir = "/tmp",          # real path, not "all"
    env_extra = json.dumps({
        "DFTRACER_INIT": "PRELOAD",
        "LD_PRELOAD": "<venv>/lib/python3.12/site-packages/dftracer/lib/libdftracer_preload.so",
        "LD_LIBRARY_PATH": "<venv>/lib/python3.12/site-packages/dftracer/lib"
    })
)

# HYBRID mode (source has DFTRACER_C_INIT + DFTRACER_C_FINI)
session_run_with_dftracer(
    run_id  = RUN_ID,
    command = "mpirun --allow-run-as-root -np 4 -x DFTRACER_ENABLE -x DFTRACER_INIT -x DFTRACER_DATA_DIR -x DFTRACER_LOG_FILE -x DFTRACER_INC_METADATA -x LD_LIBRARY_PATH -x LD_PRELOAD ./myapp_annotated",
    data_dir = "/tmp",
    env_extra = json.dumps({
        "DFTRACER_INIT": "HYBRID",
        "LD_PRELOAD": "<venv>/lib/python3.12/site-packages/dftracer/lib/libdftracer_preload.so",
        "LD_LIBRARY_PATH": "<venv>/lib/python3.12/site-packages/dftracer/lib"
    })
)
```

The `data_dir` parameter to `session_run_with_dftracer` maps to `DFTRACER_DATA_DIR`.
When the tool says `data_dir="all"` in its docstring, that is a shorthand interpreted
by the tool itself — the C++ library still receives a real path.

## Permissions

This skill uses:

- **MCP:** `mcp__dftracer__session_run_with_dftracer`
- **Bash:** `mpirun` — run only inside the active session workspace (`cwd=workspaces/<session>/...`)
- **Env:** sets `DFTRACER_*`, `LD_PRELOAD`, `DFTRACER_DATA_DIR`, `DFTRACER_LOG_FILE` (→ `workspaces/<session>/traces/`)
- **Write:** `workspaces/<session>/*` only

Never set `DFTRACER_DISABLE_IO`. Never `sudo`; never write outside the project root.
