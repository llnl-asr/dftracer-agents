---
name: workload-flashx
description: Flash-X-specific knowledge for the dftracer pipeline — build system (Python setup + GNU Make), Sedov 3D test problem, dangling symlinks in shallow clones, HDF5/flash.par pitfalls. Load this skill whenever working with Flash-X.
---

# Flash-X workload notes

Flash-X (git@github.com:Flash-X/Flash-X.git) is a Fortran/C++/C multiphysics
AMR code built with its own Python `setup` script + GNU Make (NOT cmake/autotools).

## Build system
- Top-level `./setup <Problem> -auto -<dim>` (e.g. `./setup Sedov -auto -3d`)
  generates an `object/` build dir; then `make` in that dir.
- Machine config lives under `sites/`; `Makefile.h` picks compilers/HDF5.
- Point HDF5 at a **source-built** install in the session workspace, never the
  Cray/system HDF5 module. See [[software-hdf5]] and [[feedback_always_source_hdf5]].

## Sedov 3D test problem
- `source/Simulation/SimulationMain/Sedov/` — flash.par + `tests/*.par` variants.
- Prior config: FUNCTION + DATA_DIR=all tracing, ~768 ranks, wall ≥ 30 min.

## Instrumentation strategy (Fortran-heavy code)
Flash-X is ~2600 Fortran (.F90) files vs 126 C / 6 C++ / 70 Python — dftracer's
clang auto-annotator only covers C/C++/Python. Decided approach (2026-07-08):
FUNCTION mode always; auto-annotate the C/C++ IO layer first; ASSESS I/O-path
coverage; if low, MANUALLY annotate key Fortran IO routines (`source/IO/IOMain/**`)
with dftracer's Fortran API. Always run with PRELOAD + DATA_DIR=all so HDF5/POSIX/MPI
I/O is captured at the library level regardless of source annotation. See
[[dftracer-preload-run]].

## Pitfalls (dated lessons: symptom → root cause → fix)
- 2026-07-07: `session_create` crashed with `FileNotFoundError` on
  `source/physics/sourceTerms/Stir/StirMain/TurbGen.h` → Flash-X ships **dangling
  symlinks** (targets not present in a `--depth 1` clone) and `shutil.copytree`
  dereferenced them → fix: copy with `symlinks=True, ignore_dangling_symlinks=True`
  (done in `mcp_tools/tools/session/session_tools.py` + siblings). Restart the MCP
  server after the fix.
- flash.par 80-column pitfall: long absolute output paths get silently
  truncated → keep a short `ds` symlink to the Lustre output dir and reference
  that in flash.par instead of the full path.
- Traces must land in `<WS>/traces/` (workspace), NOT the Lustre output dir —
  set `DFTRACER_LOG_FILE` to the workspace explicitly. See
  [[feedback_optimization_pipeline_traces]].
- 2026-07-08 (build): Cray PE `ftn`/`craycc` FAILED to build Flash-X (Fortran
  flag incompatibilities) → use the **GNU 11.2 MPI wrappers** at
  `/opt/cray/pe/mpich/9.0.1/ofi/gnu/11.2/bin/{mpif90,mpicc,mpicxx}` instead. Set
  `MPI_PATH=/opt/cray/pe/mpich/9.0.1/ofi/gnu/11.2` and
  `HDF5_PATH=<WS>/hdf5_1.14` in `sites/Prototypes/Linux/Makefile.h`.
- 2026-07-08 (build): gfortran strict checking vs MPI Fortran module interfaces →
  add `-fallow-argument-mismatch` to FFLAGS or the build fails on MPI type
  mismatches. Working Sedov 3D build: `./setup Sedov -auto -3d` then `make -j` in
  `object/`; exe is `object/flashx`. LD_LIBRARY_PATH must include CCE libs +
  `/usr/lib64` at link time.
- 2026-07-08 (annotation): Flash-X is Fortran-heavy (~2600 .F90 files) with C/C++
  IO layer. Fortran entry point `program Flashx` in `source/Simulation/main.F90`
  has no C `main()` for DFTRACER_C_INIT/FINI → create a C wrapper file
  `object/dftracer_init_fini.c` with `__attribute__((constructor))` and
  `__attribute__((destructor))` to auto-call DFTRACER_C_INIT/DFTRACER_C_FINI.
  Add the `.o` to `ALL_OBJ_FILES` in `object/Makefile` and ensure it links.
  See [[dftracer-annotate-general]] for the exact wrapper pattern.
- 2026-07-08 (annotation): CCE Fortran linker (`crayftn`) may not fire
  constructor/destructor attributes reliably → if FUNCTION mode produces no
  traces despite wrapper being linked, **pivot to PRELOAD mode immediately**.
  PRELOAD mode captures HDF5/POSIX I/O at the library level without requiring
  source annotations to work. Set `DFTRACER_INIT=PRELOAD`, `DFTRACER_DATA_DIR=all`,
  and use `LD_PRELOAD=<path>/libdftracer_core.so.4.1.0`.
- 2026-07-08 (runtime): CCE runtime libraries must be in `LD_LIBRARY_PATH`:
  `/opt/cray/pe/cce/20.0.0/cce/x86_64/lib` — without this, the binary fails to
  find `libcraymp.so` and other CCE runtime deps at runtime even though it links.
- 2026-07-08 (runtime): `module load` inside a bash script submitted via `flux proxy`
  HUNG indefinitely — Lmod re-loading already-loaded modules in a Flux allocation
  causes a deadlock in the Lua module interpreter. The script showed 768 bash
  processes all stuck on `/usr/bin/lua /usr/share/lmod/lmod/libexec/lmod shell
  load cray-mpich/9.0.1`. **Fix:** Do NOT load modules inside the bash script
  when running under `flux proxy` — the allocation already has the correct modules
  loaded. Verify with `module list` inside the script if needed, but never reload.
- 2026-07-08 (trace analysis): Flash-X Sedov 3D I/O pattern — primary bottleneck
  is `io_h5write_unknowns_` (32% of I/O time), secondary is the xfer pipeline
  (`io_xfer_cont_slab` → `io_h5_xfer_wrapper` → `io_h5_xfer` → `io_h5_type_matched_xfer`)
  at 26.6%. Attribute creation (`io_h5_attribute_create`, 156 calls) is 9.6%.
  Optimization: increase `checkpointFileIntervalTime` (default 0.01 → 0.05),
  reduce `plot_var_N` count, enable HDF5 collective I/O.

## Build Session 2026-07-08: Flash-X Sedov 3D Baseline

Successfully built Flash-X Sedov 3D baseline executable on Tuolumne.

**Steps:**
1. Built HDF5 1.14.3 from source (curl from HDF5 FTP mirror) into workspace
2. Patched HDF5 header (H5Apublic.h line 932: chid_t → hid_t)
3. Initialized PARAMESH submodule before setup
4. Ran setup with HDF5_PATH env var: `HDF5_PATH=<WS>/hdf5_1.14 ./setup Sedov -auto -3d`
5. Updated object/Makefile.h HDF5_PATH to workspace
6. Built with `make -j4` in object dir

**Result:**
- Executable: `<WS>/baseline/source/object/flashx` (6.6M, dynamically linked)
- Exit code: 0 (success)
- Requires LD_LIBRARY_PATH to include `<WS>/hdf5_1.14/lib` at runtime

**Critical lessons:**
- PARAMESH submodule MUST be initialized before setup or it fails
- HDF5_PATH env var is honored by setup script to override Makefile.h defaults
- GNU 11.2 MPI wrappers are used (auto-detected, working correctly)
- No code errors, build completed successfully on first attempt after setup

## Large-Scale Production Run Configuration

**MANDATORY: Every baseline and optimization iteration must run on the user's active allocation with ALL nodes, using Lustre for I/O.**

### Problem Size Requirements
To produce meaningful optimization results, the problem must be large enough to:
1. **Use ~50% of node memory** (~250 GB per node on Tuolumne)
2. **Run for at least 30 minutes** (to amortize startup overhead)
3. **Generate substantial I/O** (checkpoint files should be multi-GB)

### Flash-X Grid Sizing Formula
Flash-X memory per cell ≈ 200 bytes (conservative estimate for Sedov 3D with AMR).
For 8 nodes × 250 GB target = 2 TB total:
- Total cells needed: 2 TB / 200 B ≈ 10 billion cells
- Uniform grid: cube_root(10B) ≈ 2150³
- With AMR (lrefine_max=6, typical 2-4× refinement): base grid ~ 512³ to 1024³

**Recommended large-scale config for 8 nodes:**
```
# Grid parameters (uniform base grid, AMR refines up to lrefine_max)
iProcs = 8          # 8 MPI ranks in X (1 per node)
jProcs = 1
kProcs = 1
# OR for better decomposition:
iProcs = 2
jProcs = 2
kProcs = 2

# With AMR, effective resolution can be much higher
lrefine_max = 6

# I/O parameters — frequent checkpoints to generate I/O load
checkpointFileIntervalTime = 0.05   # checkpoint every 0.05 time units
checkpointFileIntervalStep = 0
nend = 100000
tmax = 0.5                          # longer runtime

# Output to Lustre
basenm = "/p/lustre5/haridev/flashx/sedov_"
```

### Allocation-Aware Run Rules
1. **ALWAYS ask the user for their active allocation ID** before any large run.
2. **Use ALL nodes in the allocation** (`flux run -N <nnodes> -n <ntasks> --exclusive`).
3. **Route I/O to Lustre** — set `basenm` in flash.par to a path under `/p/lustre5/$USER/`.
4. **Create the Lustre output directory before running** (`mkdir -p /p/lustre5/$USER/flashx`).
5. **Verify the allocation is still active** with `flux jobs` before submitting.
6. **Wall time**: request at least 60 minutes for a 30-minute app run (dftracer overhead + safety margin).

### Flux Proxy Run Pattern (MANDATORY)

**Never pass environment variables inline with `flux proxy <id> flux run -x VAR`.**
`flux proxy` opens an SSH tunnel to the allocation broker; environment variables
set in the local shell are **not** automatically forwarded through the proxy.
Instead, **wrap the entire run in a bash script** and invoke that script via
`flux proxy`:

```bash
# 1. Create a run script that exports ALL env vars internally
# NOTE: Do NOT load modules inside the script — the Flux allocation already has
# the correct modules loaded. Re-loading modules via Lmod inside a flux proxy
# job causes a deadlock in the Lua interpreter.
cat > production_run.sh << 'EOF'
#!/bin/bash
set -e

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
# Use 48 processes per node (not 96) to avoid oversubscription issues
flux proxy <alloc_id> flux run -N <nnodes> -n $((<nnodes> * 48)) --exclusive ./production_run.sh
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

### Lustre I/O Best Practices for Flash-X
- Use a dedicated subdirectory per run: `/p/lustre5/$USER/flashx/<run_name>/`
- Set `HDF5_USE_FILE_LOCKING=FALSE` to avoid Lustre file locking contention
- For collective I/O at scale, set `ROMIO_CB_WRITE=enable` and `CB_BUFFER_SIZE=16777216`
- Lustre stripe count should match or exceed the number of OSTs (typically 4-8 on Tuolumne)

### Paper Search for Production Configuration (MANDATORY)

Before configuring any production-scale Flash-X run, **search academic papers** for the specific problem setup to find validated configurations. The Flash-X source tree contains parameter files, but they are often small test cases. Papers provide:

1. **Validated problem sizes** that produce meaningful I/O and runtime
2. **AMR refinement parameters** (lrefine_max, refine_cutoff, etc.)
3. **Scaling studies** that inform rank count and decomposition decisions
4. **I/O patterns** (checkpoint frequency, file sizes, collective vs independent)

**How to search (Tool-First Rule):**
1. **Call the MCP tool first:** `mcp_dftracer2_session_search_papers_for_config` with:
   - `app_name="Flash-X"`
   - `problem_name="Sedov"` (or whatever problem you are running)
   - This searches arXiv + Semantic Scholar, extracts known parameter patterns, and persists results to `session.json`
2. **Fallback:** If the tool is unavailable, use `mcp_dftracer2_search_papers_combined` manually with queries like:
   - `"Flash-X Sedov AMR simulation configuration production HPC"`
   - `"Flash-X checkpoint I/O performance optimization parallel HDF5"`
   - `"FLASH AMR Sedov scaling study"`
3. Also search the local paper library with `mcp_dftracer2_session_search_local_papers`
4. Read the full paper (arXiv PDF) to extract specific parameter values

**Key paper found for Flash-X checkpoint/restart:**
- *"Checkpoint/Restart for Lagrangian particle mesh with AMR in community code FLASH-X"* — Jain et al., 2021, arXiv:2103.04267v1
- Covers cross-mesh checkpoint-restart, AMReX and Paramesh formats, HDF5 I/O strong/weak scaling
- Relevant for understanding checkpoint I/O patterns and expected file sizes

### Correct Flash-X Grid Configuration (Paramesh AMR)

**Critical discovery (2026-07-08):** The `nblockx`, `nblocky`, `nblockz` parameters in `flash.par` control the **initial number of blocks per direction** in Paramesh AMR mode. These are NOT the same as `iProcs/jProcs/kProcs` (which control MPI decomposition).

**For a large-scale production run with AMR:**
```
# Initial block distribution (controls base problem size)
nblockx = 9
nblocky = 9
nblockz = 9
# Total initial blocks: 9³ = 729 blocks
# With NXB=8 (compile-time), each block is 8³ cells
# Total initial cells: 729 × 512 = 373,248 cells
# AMR will refine this up to lrefine_max levels
```

**Reference configurations from Flash-X source:**
- `flash.bgp.2048.par`: nblockx/y/z=9, lrefine_max=7, nend=4, tmax=0.05
- `flash.bgp.4096.par`: lrefine_max=7, lrefine_min=6, checkpointFileIntervalTime=0.03, wall_clock_time_limit=2700
- `flash.bgp_bench.4096.par`: nblockx/y/z=9, lrefine_max=7, useCollectiveHDF5=.true.

**Key insight:** The 2048-rank and 4096-rank benchmark configs use **nblockx/y/z=9** with relatively short runtimes (nend=4, tmax=0.05). For a 30+ minute production run, increase `nend` and `tmax` proportionally while keeping the same block structure.

### Smoke Test vs Production Run
| Aspect | Smoke Test | Production Run |
|--------|-----------|----------------|
| Grid size | 16³ (tiny) | nblockx/y/z=9 with AMR |
| MPI ranks | 1 | All nodes (8–768) |
| Runtime | < 5 seconds | ≥ 30 minutes |
| I/O volume | < 1 MB | Multi-GB checkpoints |
| Purpose | Verify annotation/build | Measure optimization impact |
| Allocation | Any single node | User's active allocation |

**Never compare a smoke test against a production run.** Always compare baseline vs optimization within the same run class (both smoke or both production).

## Configuration Validation Checklist (MANDATORY before every production run)

The following checklist MUST be completed before submitting any production-scale Flash-X run. Missing any item will produce meaningless results (tiny checkpoints, short runtime, or outright failure).

### Pre-Run Verification Steps

1. **Verify binary was compiled with the expected NXB value**
   - Check the build log for compile flags: `grep -E 'NXB|NYB|NZB' <build_log>`
   - Expected: `-DNXB=8 -DNYB=8 -DNZB=8` (or `-DNXB=32` if rebuilt)
   - **Pitfall:** The Makefile may show NXB=32 but the actual binary was built with NXB=8. Always verify the build log, not just the Makefile.

2. **Verify flash.par uses Paramesh AMR mode (NOT uniform grid)**
   - Paramesh AMR: `iProcs=1, jProcs=1, kProcs=1` with explicit `nblockx/y/z`
   - Uniform grid: `iProcs×jProcs×kProcs = total_ranks`, no explicit `nblockx/y/z`
   - **Why it matters:** Uniform grid with default nblockx/y/z=1 produces ~140KB checkpoints. Paramesh AMR with nblockx/y/z=9 produces multi-GB checkpoints.
   - **Pitfall:** The `flash_production.par` file was accidentally modified to use uniform grid mode after the run, which would fail or produce tiny checkpoints.

3. **Verify checkpoint size expectations**
   - After the first checkpoint, check file size: `ls -lh <lustre_out>/*chk_*`
   - Expected: ≥ 500MB per checkpoint for meaningful I/O tracing
   - If checkpoints are < 10MB, the problem is too small — increase nblockx/y/z or lrefine_max

4. **Verify runtime expectations**
   - After 5 minutes of wall time, check the log for step count: `grep 'n=' sedov_production.log | tail -5`
   - Expected: ≥ 50 steps in first 5 minutes (AMR timesteps are small)
   - If < 10 steps, the problem may be too small or CFL too restrictive

5. **Verify DFTracer trace files are being generated**
   - Check trace directory: `ls -lh <ws>/production_baseline/traces/raw/`
   - Expected: `.pfw` or `.pfw.gz` files growing in size during the run
   - If no trace files, check LD_PRELOAD path and DFTRACER_ENABLE=1

### Configuration Parameters Reference (Validated)

Based on Flash-X benchmark files (`flash.bgp.2048.par`, `flash.bgp_bench.4096.par`) and weak scaling I/O tests (`sedov_io_*_32b_3d_weak.par`):

| Parameter | Benchmark Value | Production Value (384 ranks) | Notes |
|-----------|----------------|-------------------------------|-------|
| `nblockx` | 9 | 9 | Initial blocks in X (from benchmark) |
| `nblocky` | 9 | 9 | Initial blocks in Y (from benchmark) |
| `nblockz` | 9 | 9 | Initial blocks in Z (from benchmark) |
| `iProcs` | 1 | 1 | Paramesh AMR mode (always 1) |
| `jProcs` | 1 | 1 | Paramesh AMR mode (always 1) |
| `kProcs` | 1 | 1 | Paramesh AMR mode (always 1) |
| `lrefine_max` | 7 | 6 | Reduced from 7 to avoid memory pressure at 384 ranks |
| `lrefine_min` | — | 4 | Added to ensure minimum refinement |
| `sim_rInit` | — | 0.013671875 | Calibrated for NXB=8 (1 cell radius) |
| `checkpointFileIntervalTime` | 0.03 | 0.03 | From benchmark; ~16 checkpoints for tmax=0.5 |
| `tmax` | 0.05 | 0.5 | Extended for 30+ min runtime |
| `nend` | 4–10000 | 1000000 | High enough that tmax or wall time stops first |
| `wall_clock_time_limit` | 2700 | 3600 | 60 minutes for 30-minute app run |
| `useCollectiveHDF5` | .true. | .true. | Required for scale |

### Common Configuration Mistakes and Fixes

| Mistake | Symptom | Fix |
|---------|---------|-----|
| Uniform grid mode (iProcs>1) with default nblockx/y/z=1 | Tiny checkpoints (~140KB), short runtime | Switch to Paramesh AMR: iProcs=jProcs=kProcs=1, nblockx/y/z=9 |
| NXB mismatch (binary built with NXB=8, par file assumes NXB=32) | Incorrect cell sizes, possible crash | Verify build log, update sim_rInit to match actual NXB |
| lrefine_max too high for rank count | Out-of-memory crash | Reduce lrefine_max (e.g., 7→6 for 384 ranks) |
| checkpointFileIntervalTime too large | Few checkpoints, insufficient I/O data | Reduce to 0.03 (from benchmark) |
| tmax too small | Run finishes in < 5 minutes | Increase tmax to 0.5 |
| Missing lrefine_min | Coarse regions may cause numerical issues | Add lrefine_min=4 |

### Paper-Backed Configuration Discovery (Tool-First Rule)

Before configuring ANY production-scale Flash-X run, search for validated configurations in academic papers. The MCP tool `mcp_dftracer2_session_search_papers_for_config` should be called first. If unavailable, use these manual search queries:

**arXiv / Semantic Scholar queries:**
- `"Flash-X Sedov AMR simulation configuration production HPC"`
- `"Flash-X checkpoint I/O performance optimization parallel HDF5"`
- `"FLASH AMR Sedov scaling study"`
- `"Flash-X weak scaling I/O benchmark"`

**GitHub repository search (Flash-X/Flash-X):**
- Search `source/Simulation/SimulationMain/Sedov/extraParfiles/` for benchmark parameter files
- Key files: `flash.bgp.2048.par`, `flash.bgp_bench.4096.par`, `sedov_io_*_32b_3d_weak.par`
- These contain validated parameters from the Flash-X development team

**Key paper:**
- *"Checkpoint/Restart for Lagrangian particle mesh with AMR in community code FLASH-X"* — Jain et al., 2021, arXiv:2103.04267v1
- Covers checkpoint I/O patterns, AMReX and Paramesh formats, HDF5 scaling
- Use this to understand expected checkpoint sizes and I/O patterns

### Self-Learning Update Rule

After every production run, update this skill with:
1. Actual checkpoint sizes observed (verify expectations)
2. Actual runtime and step count (verify expectations)
3. Any configuration changes that were needed and why
4. New benchmark parameter files discovered in the Flash-X repo
5. Paper references that provided useful configuration guidance


## Additional Lessons (2026-07-08)

- **session_configure incompatibility:** The standard MCP `session_configure` tool fails
  on Flash-X because it's NOT a standard Python package. The tool attempts `pip install -e`
  but Flash-X's repository root lacks both `setup.py` and `pyproject.toml`.
  
  **Why it fails:** Flash-X uses a custom Python-based setup script (`./setup <Problem>`)
  that generates a GNU Make build system, not a pip-installable package.
  
  **Correct procedure for Flash-X setup:**
  1. Initialize all git submodules: `git submodule update --init --recursive`
  2. Run the Flash-X setup script: `bash setup Sedov -auto -3d` (generates `object/` dir)
  3. Customize `object/Makefile.h` with paths and compiler flags
  4. Build with `make -j <ncores>` in the `object/` directory
  
  The documentation in SESSION_CONFIGURATION.md (generated 2026-07-08) has the full build procedure.


- 2026-07-08 (HDF5 setup fallback): Flash-X setup script accepts HDF5_PATH pointing to source-only
  directory (with no lib/ subdirectory built) and silently falls back to system HDF5 instead of
  failing or warning → `bash setup Sedov -auto -3d` with HDF5_PATH=/path/to/source generated
  Makefile.h that still tried to link against the source dir, but linker couldn't find libhdf5
  and fell back to /lib64/libhdf5.so.103 (system HDF5 1.10.5) → binary runs but uses old HDF5 API
  → **Fix for next build:** Build + install HDF5 1.14.5 to `hdf5_install/lib/` BEFORE running
  setup, or verify with `test -d $HDF5_PATH/lib && test -f $HDF5_PATH/lib/libhdf5.so*` before
  proceeding. If fallback is unavoidable, check actual library linked at runtime with `ldd flashx | grep hdf5`
  and update documentation if system HDF5 is acceptable for the baseline (it is, for tracing purposes).
  → **Common trigger (confirmed 2026-07-08):** HDF5_PATH set to a SOURCE-ONLY directory (e.g. the
  hyphenated `hdf5-1.14.5` unpack dir with no built `lib/`/`include/`) — point HDF5_PATH at the
  BUILT/installed prefix (e.g. `hdf5_1.14`) instead. After a clean rebuild (`make distclean && make -j`),
  a correct link shows `<WS>/hdf5_1.14/lib/libhdf5.so.310`, never `/lib64/libhdf5.so.103`.


- 2026-07-08 (dftracer install on Tuolumne): Successfully installed dftracer develop with session-built HDF5 1.14.5 and Cray MPICH 9.0.1 (GNU 11.2 wrappers). HIP disabled (CPU-only workload). All libraries (libdftracer_preload.so, libdftracer_core.so) verified clean with ldd. Session HDF5 1.14.5 and Cray MPI correctly linked. Installation paths resolved: $DFTRACER_LIB_DIR=/usr/WS2/haridev/dftracer-agents/workspaces/flash_x/20260708_201403/install/lib/python3.13/site-packages/dftracer/lib64 (see artifacts/01_dftracer_install_verification.log for full details). NOTE: MPI is compatible on tuolumne — MPI-IO tracing should be enabled by passing the MPI version + headers; do not accept "auto-disabled/unsupported" (user-confirmed misdiagnosis).

- 2026-07-08 (setup wipes object/): `bash setup Sedov -auto -3d` REGENERATES the `object/` build dir
  from scratch every time, wiping any hand-added files. → **Fix:** apply Makefile.h edits AND recreate
  any shim files (e.g. the dftracer `dftracer_init_fini.c` constructor/destructor shim, `MACHOBJ +=
  dftracer_init_fini.o`) AFTER running setup, never before. If you re-run setup, re-apply them.

- 2026-07-08 (output paths are cwd-relative): Flash-X resolves `basenm`/output paths (checkpoints,
  plotfiles, `sedov.dat`) relative to the PROCESS cwd, not the workspace root. Under a job launcher the
  run cwd is `object/`. → **Fix:** the `ds -> /p/lustre<N>/$USER/flashx` output symlink must exist in the
  RUN cwd (`object/`), not only at the WS root. Create `ds` in `object/` before running, or you get
  `Fortran runtime error: Cannot open file 'ds/.../sedov.dat'`. (Keep the flash.par 80-column limit in mind —
  use the short `ds` symlink for the output path.)

- 2026-07-08 (annotated smoke SUCCESS): FUNCTION-mode instrumentation works end-to-end. Annotated flashx
  (66 C IO files + init/fini shim) ran Sedov 3D single-rank and emitted a 6566-event trace:
  HDF5 5236, POSIX 696, C_APP 552, dftracer 43, STDIO 36, MPI 3. No PRELOAD pivot needed. ldd shows session
  HDF5 1.14.5 (libhdf5.so.310) + libdftracer_core.so.4.1.0 (RPATH) + cray-mpich gnu/11.2.
