---
name: system-tuolumne
description: System profile for Tuolumne (LLNL AMD MI300A / Cray PE) — module load order, Flux scheduler, ROMIO/MPICH-IO hints, Lustre striping, ROCm/PyTorch environment setup, and known build/run pitfalls
---

# System: Tuolumne

AMD MI300A APU cluster at LLNL. Uses Cray PE with ROCm.

See also [[software-mpifileutils]] — for any large-scale (multi-GB/TB, or >10k files)
copy/sync/delete/compare/archive/restripe operation on `/p/lustre5`, use the mpifileutils
tools (`dcp`/`dsync`/`drm`/`dwalk`/`dcmp`/`dtar`/`dstripe`/etc.) instead of serial
`cp`/`rsync`/`rm -rf`/`diff -r`/`tar` — they parallelize the directory walk across MPI ranks,
which is the actual bottleneck at this scale on a shared parallel filesystem.

## Key Constraints

- **No sudo** — unprivileged user environment only.
- MPI uses `cray-mpich/9.0.1` via OFI fabric (`craype-network-ofi`, `libfabric/match_SHS`).
- Compiler: CCE 20.0.0 (`cce/20.0.0`) under `PrgEnv-cray/8.7.0`.
- **GPU clock/perf level is not tunable as a non-root user on compute nodes.**
  `/sys/class/drm/card*/device/power_dpm_force_performance_level` reads `auto` and is
  NOT-WRITABLE; `rocm-smi` is not on `PATH` even after `module load rocm/<version>`. Confirmed
  2026-07-25. Check quickly with:
  `for f in /sys/class/drm/card*/device/power_dpm_force_performance_level; do cat $f; test -w $f && echo WRITABLE || echo NOT-WRITABLE; done`
  — do not propose GPU-frequency/perf-level tuning as an optimization lever on this system.
- Python: `python/3.13.2`

## RCCL/NCCL multi-node GPU-collective training defaults to the wrong (1GbE) NIC

Confirmed 2026-07-25 (ray_molformer session): `ibv_devices` returns zero devices (no IB
verbs transport), and `/opt/rocm-*/lib` ships no `librccl-net*`/`aws-ofi-rccl` plugin (no
libfabric/CXI path for RCCL, even though `fi_info` shows a healthy `cxi` provider — that
path is only reachable through the missing plugin). RCCL therefore falls back to its TCP
**socket** transport, and its default interface-prefix scan (`ib*`, `eth*`, `en*`, `em*`,
`bond*`) matches `enp129s0` (the **1000 Mb/s** management NIC) before it would ever reach
`hsi0` (the **200000 Mb/s** Slingshot-11 fabric NIC). Any multi-node RCCL/torch-DDP job that
doesn't explicitly pin the interface is likely running inter-node collectives at ~1/200th of
available bandwidth, silently.

**Fix**: always export `NCCL_SOCKET_IFNAME=hsi0` before launching multi-node NCCL/RCCL
collective training on Tuolumne. Confirm the actual selection once with
`NCCL_DEBUG=INFO NCCL_DEBUG_SUBSYS=INIT,NET` in the launch env — this logs the chosen
transport and NIC. `NCCL_NET_GDR_LEVEL` and other libfabric/CXI-path tuning are inert without
the `aws-ofi-rccl` plugin installed (https://github.com/ROCm/aws-ofi-rccl) — check for that
plugin before assuming any CXI-path RCCL env var will do anything.

## Module Load Sequence

Load modules in this order (order matters for Cray PE):

```bash
module load craype-x86-trento
module load libfabric/match_SHS
module load craype-network-ofi
module load perftools-base/25.09.0
module load craype/2.7.35
module load PrgEnv-cray/8.7.0
module load flux_wrappers/0.1
module load xpmem/2.6.5
module load cce/20.0.0
module load cray-libsci/25.09.0
module load cray-mpich/9.0.1
module load python/3.13.2
```

StdEnv (S) is loaded by default — do not reload it.

## LD_LIBRARY_PATH Fix for CCE

After loading modules, set:

```bash
GCC_MODULE="cce/20.0.0"
export LD_LIBRARY_PATH="/opt/cray/pe/${GCC_MODULE}/cce/x86_64/lib:/opt/cray/pe/${GCC_MODULE}/cce/x86_64/lib/default64:/usr/lib64:${LD_LIBRARY_PATH}"
echo "Updated LD_LIBRARY_PATH for CCE: $LD_LIBRARY_PATH"
```

### Pitfall: anaconda `compiler_compat/ld` breaks native C/C++ builds (undefined ZSTD_* references)

When building native extensions via a Python from
`/collab/usr/gapps/python/toss_4_x86_64_ib/anaconda3-*` (e.g. `pip install -e .`
for `dftracer-utils`/`dftracer-agents`), the link step can fail with errors like:

```
.../anaconda3-2025.3.1/compiler_compat/ld: lib/librocksdb.so.10.10.1: undefined reference to `ZSTD_CCtx_setParameter'
.../anaconda3-2025.3.1/compiler_compat/ld: lib/libdftracer_utils_utilities.so.0.0.10: undefined reference to `ZSTD_compress'
```

**Root cause**: PATH resolution picks anaconda's `compiler_compat/ld` ahead of
the Cray `ld` (`/opt/cray/pe/cce/20.0.0/binutils/.../ld`). That `ld` was built
with a relocatable/placeholder default sysroot, so its built-in
`SEARCH_DIR("=/usr/lib64")` resolves to a nonexistent placeholder path instead
of the real `/usr/lib64` — even though `/usr/lib64/libzstd.so` (with all the
needed symbols) is present on the system. GNU `ld` also consults
`LD_LIBRARY_PATH` as a fallback search path, so including `/usr/lib64` there
(as in the export above) is sufficient to work around the broken sysroot
without needing to touch PATH or find a different `ld`.

**Verify the fix** before a full rebuild:

```bash
echo 'extern void *ZSTD_createCDict(const void*, unsigned long, int); int main(){ZSTD_createCDict(0,0,0);return 0;}' > /tmp/zstd_test.c
gcc /tmp/zstd_test.c -o /tmp/zstd_test -lzstd \
  -B/collab/usr/gapps/python/toss_4_x86_64_ib/anaconda3-2025.3.1/compiler_compat
# Should link and run cleanly once LD_LIBRARY_PATH includes /usr/lib64.
```

### Recurring gotcha: a session-local dftracer install also needs its lib64 dir on LD_LIBRARY_PATH at EVERY step, not just build

Observed repeatedly across a single session (2026-07-16, flux-fiction): fixing
`LD_LIBRARY_PATH` for one step (e.g. the C plugin build) does not carry
forward to the next step (Python import, run) — each new subprocess/agent
invocation needs it re-exported, including the session-local dftracer's own
`<session_venv>/lib/python3.13/site-packages/dftracer/lib64` (or wherever its
C libs land), in addition to the CCE/`/usr/lib64` paths above. Locate it with
`find <session_venv> -name 'libdftracer_core.so*'` if unsure. If this keeps
recurring step-to-step within one session, prefer baking an `-Wl,-rpath,...`
into the build (so the binary finds its libs without any `LD_LIBRARY_PATH` at
run time) over re-exporting the same value at every step — raise this as a
tooling fix if it keeps happening.

## I/O and Workspace

- **All benchmark I/O must target Lustre**: use `/p/lustre5/$USER/workspaces/ior/` as the data directory for IOR runs and trace output. This path is read/write accessible and is the correct path for high-bandwidth parallel I/O workloads.
- Smoke tests and benchmark runs must pass `DATA_DIR=/p/lustre5/$USER/workspaces/ior` (or the equivalent `--output-file` / `-o` flag for IOR) so that files land on Lustre, not on the shared home filesystem.
- Use `workspaces/<session>/tmp/` inside the project directory for build artifacts and temporary files.
- Never write to `/tmp` directly.

```bash
# Create Lustre workdir before any IOR run
mkdir -p /p/lustre5/$USER/workspaces/ior
```

### Archiving bulky traces off the workspace to `/p/vast1` (2026-08-27)

The NFS workspace filesystem is small and fills up; accumulated dftracer traces are usually
the largest thing on it (one project held 767 GB across 344 `traces/` dirs, ~90% of it in
just 8 directories). `/p/vast1/$USER` is the archive target — multi-PB, and writable.

Relocate the bulky ones and symlink back, preserving the workspace-relative path so
`<WS>/traces/` still resolves and the pipeline tooling keeps working:

```bash
dst=/p/vast1/$USER/dftracer-agents/$rel     # $rel = workspaces/<app>/<session>/.../traces
mkdir -p "$(dirname "$dst")"
rsync -a "$src/" "$dst/"
# verify (see software-mpifileutils), THEN:
rm -rf "$src" && ln -s "$dst" "$src"
```

This matches the existing `dataset` → PFS symlink convention already used across sessions.

**Two caveats:**
- Verify with regular-file bytes + an rsync dry-run, never a `du` comparison — the workspace
  NFS and VAST report **different directory-inode sizes**, which produces a phantom mismatch
  on every directory. Full details in [[software-mpifileutils]].
- Traces written *through* such a symlink land on VAST NFS, not the workspace. That conflicts
  with the traces-stay-in-workspace rule ([[feedback-lustre-io]],
  [[feedback-optimization-pipeline-traces]]). Fine for archived/finished sessions; think
  twice before reusing one for a live optimization loop.

Sizing first is worth it — trace size is extremely top-heavy, so moving the top handful
recovers nearly all the space for a fraction of the symlinks and NFS round-trips.

### Rabbit near-node flash accelerators

Tuolumne compute nodes have **Rabbit** node-local NVMe accelerators that can be
provisioned as XFS, GFS2, or Lustre scratch to front the network Lustre path and
accelerate data access. Request them via DataWarp directives passed to Flux with
`-S "#DW jobdw ..."`. For the tier decision guide (SHM → XFS → GFS2 → Lustre by
sharing scope), `--coral2-chassis=1` usage, and L3 optimization workflow, load
[[system-tuolumne-rabbit]].

## Scheduler: Flux

Tuolumne uses **Flux** as its job scheduler. Do not use `srun` or `mpirun` directly.

- Allocate nodes, list queues, and run jobs via Flux — see the `/flux-alloc` skill for full syntax.
- `flux_wrappers/0.1` must be loaded (included in the module sequence above) before any `flux` command.
- MPI jobs are launched with `flux run -N <nodes> -n <tasks> <cmd>` inside an allocated Flux instance.

Quick reference:

```bash
flux queue list                               # list queues and availability
flux resource info                            # total nodes/cores/GPUs
flux alloc -N <N> -q <QUEUE> -t <TIME>       # allocate nodes interactively
flux alloc --bg -N <N> -q <QUEUE> -t <TIME>  # allocate in background → JOBID
flux proxy <JOBID>                            # connect to the allocation
flux run -N <N> -n <TASKS> <CMD>             # run inside the allocation
```

For a full workflow (queue discovery → alloc → proxy → job submission), invoke the `/flux-alloc` skill.

## ROMIO / MPICH_MPIIO_HINTS on Cray MPICH 9.0.1

### How to discover available hints on a new MPI

Always query the MPI library first when on a new system or MPI version:

```bash
strings /opt/cray/pe/mpich/9.0.1/ofi/cray/20.0/lib/libmpi_cray.so \
  | grep -E "^romio_|^cb_|^striping_|^ind_|^ds_" | sort -u
```

### Cray MPICH 9.0.1 — confirmed available hints

Verified by `strings` on `libmpi_cray.so`:

| Hint | Description |
| ---- | ----------- |
| `romio_cb_write` | Enable collective buffering for writes (`enable`/`disable`) |
| `romio_cb_read` | Enable collective buffering for reads |
| `romio_ds_write` | Enable data sieving for writes (`enable`/`disable`) |
| `romio_ds_read` | Enable data sieving for reads |
| `cb_buffer_size` | Collective buffer size in bytes (e.g. `67108864` = 64 MB) |
| `cb_nodes` | Number of aggregator processes |
| `cb_config_list` | Explicit aggregator list |
| `striping_factor` | Lustre stripe count (match OST count) |
| `striping_unit` | Lustre stripe size in bytes (e.g. `4194304` = 4 MB) |
| `ind_rd_buffer_size` | Independent read buffer size |
| `ind_wr_buffer_size` | Independent write buffer size |
| `romio_lustre_cb_lock_ahead_write` | Lock-ahead for collective writes |
| `romio_lustre_cb_lock_ahead_read` | Lock-ahead for collective reads |
| `romio_lustre_cb_lock_ahead_num_extents` | Number of lock-ahead extents |
| `romio_no_indep_rw` | Force collective I/O even for independent calls |

### MPICH_MPIIO_HINTS format

**Syntax:** `pathname_pattern:key=value:key=value,...` (colon-separated key=value pairs per file pattern; comma to separate multiple file patterns)

```bash
# Match all files with wildcard, colon-separated hints:
export MPICH_MPIIO_HINTS="*:romio_cb_write=enable:cb_buffer_size=67108864:cb_nodes=16:romio_ds_write=disable:striping_factor=16:striping_unit=4194304"

# Different hints per file:
export MPICH_MPIIO_HINTS="/path/to/file1:cb_nodes=8,/path/to/file2:cb_nodes=16"
```

**Always use a wrapper script** — never pass `MPICH_MPIIO_HINTS` via `--env` to `flux proxy flux run` because the colons are misinterpreted. See `/flux-alloc` skill for the wrapper script pattern.

**To display which hints MPICH actually applied**, set before running:

```bash
export MPICH_MPIIO_HINTS_DISPLAY=1
```

---

## MPI Library Path (Pitfall)

When running inside a flux allocation (`flux proxy <JOBID> flux run ...`), the compute nodes do NOT inherit a full `LD_LIBRARY_PATH`. The Cray MPI library must be added explicitly.

**Required path for cray-mpich/9.0.1:**

```bash
MPI_LIB=/opt/cray/pe/mpich/9.0.1/ofi/cray/20.0/lib
CCE_LIB=/opt/cray/pe/cce/20.0.0/cce/x86_64/lib
```

Always pass LD_LIBRARY_PATH explicitly with `--env` to `flux run`:

```bash
flux run -N <N> -n <TASKS> \
  --env LD_LIBRARY_PATH=${CCE_LIB}:${CCE_LIB}/default64:${MPI_LIB}:/opt/cray/pe/lib64:${EXTRA_LIBS} \
  <CMD>
```

Without this, all 768 ranks will fail with: `libmpi_cray.so.12: cannot open shared object file`.

## MPI Scaling (Default Behavior)

For MPI runs inside a flux allocation, always use all available physical cores:

- Tuolumne nodes have **96 physical cores** (no hyperthreading on MI300A).
- Default: `-N <all_nodes> -n <N_nodes × 96>`.
- Discover at runtime:

  ```bash
  N_NODES=$(flux proxy <JOBID> flux resource list --format="{nnodes}" -s free | tail -1)
  N_TASKS=$((N_NODES * 96))
  flux proxy <JOBID> flux run -N $N_NODES -n $N_TASKS <CMD>
  ```

When connecting via `flux proxy <JOBID>`, use all nodes in the allocation by default — do not request fewer nodes unless the user explicitly asks.

## Debugging: Core Dumps

On all Livermore systems (Tuolumne and others), enable core dumps in the directory where the job runs to capture crash state:

```bash
ulimit -c unlimited
cd /path/to/run/directory   # core file lands here
# then run the application
```

The core file appears as `core.<pid>` (or just `core`) in the current directory. Inspect with:

```bash
gdb <binary> core.<pid>
# or with ROCm for GPU faults:
rocgdb <binary> core.<pid>
```

**Why this works:** Livermore compute nodes have `ulimit -c 0` by default (no core dumps). Setting `ulimit -c unlimited` before the run re-enables them for the shell and all child processes, including MPI ranks launched via `flux run`. The core file is written to the working directory of the crashing process — make sure that path is on Lustre (`/p/lustre5/$USER/...`) and has sufficient space.

For MPI jobs where all ranks may crash, expect one core file per crashing rank.

## Deep Learning / PyTorch (ROCm) Workloads

Tuolumne apps that use PyTorch load ROCm as an environment module. The typical
pattern seen in app install and job scripts (`scripts/install-*.sh`, `*.job`):

```bash
ml cce/21.0.0 cray-mpich/9.1.0 rocm/7.1.1 rccl/fast-env-slows-mpi
```

ROCm path: `/opt/rocm-7.1.1`  
Install extras: `pip install .[rocmwci]` (uses pre-built WCI wheel, no `--no-binary=mpi4py` needed on Tuolumne)

### Library ABI issues for PyTorch on Tuolumne

Do NOT use `LD_PRELOAD` to work around library issues. Fix the root cause instead:

| Error | Root cause | Fix |
| ----- | ---------- | --- |
| libmagma error on import | libomp.so not found via rpath | Use `patchelf --add-rpath /opt/rocm-7.1.1/llvm/lib <wheel>.so` |
| segfault on mpi4py import | Wrong libmpi SONAME in wheel | `patchelf --replace-needed libmpi_gnu_112.so.12 libmpi_gnu.so.12 <torch_lib>/*.so*` |
| `undefined symbol: cblas_gemm_f16f16f32` | MKL not on rpath | `patchelf --add-rpath /opt/intel/oneapi/mkl/2024.2/lib <torch_lib>/*.so*` |

See `scripts/install-tuolumne.sh` for the canonical patchelf loop pattern.

### MIOpen warmup optimisation

Add these to skip slow direct-convolution benchmarking during warmup:

```bash
export MIOPEN_DEBUG_CONV_DIRECT_NAIVE_CONV_FWD=0
export MIOPEN_DEBUG_CONV_DIRECT_NAIVE_CONV_BWD=0
export MIOPEN_DEBUG_CONV_DIRECT_NAIVE_CONV_WRW=0
```

### patchelf fix for mpi4py inside PyTorch wheel

After installing the WCI wheel, patch any `.so` that still references the old
`libmpi_gnu_112.so.12` SONAME:

```bash
TORCH_LIB_DIR=".venvs/<venv>/lib/python3.11/site-packages/torch/lib"
for f in "$TORCH_LIB_DIR"/*.so*; do
  if patchelf --print-needed "$f" 2>/dev/null | grep -Fxq "libmpi_gnu_112.so.12"; then
    patchelf --replace-needed "libmpi_gnu_112.so.12" "libmpi_gnu.so.12" "$f"
  fi
done
```

### dftracer ROCm detection

`session_detect` scans app install/job scripts for `rocm/X.Y.Z` module-load
patterns to find the ROCm version first (no need to have the module
pre-loaded). `DFTRACER_ENABLE_HIP_TRACING=ON` is set based on whether the APP
SOURCE itself references HIP/ROCm APIs (`hip_runtime.h`, `hipMalloc`,
`find_package(HIP)`, `.hip` files, …) — NOT merely on whether ROCm is present
on the node, since every Tuolumne node has ROCm installed regardless of what
the app does (see [[bug_hip_tracing_false_positive]]).

**ROCm version fallback bug (fixed 2026-08-04, RAJAPerf session).** When no
app script names an explicit `rocm/X.Y.Z`, `_detect_rocm()`'s filesystem
fallback used to `sorted()` the `/opt/rocm-X.Y.Z` directories lexically
ascending and return the FIRST match — which is the OLDEST install on disk
(`/opt/rocm-4.2.0`), not the newest. Tuolumne keeps every ROCm release from
4.2.0 up through 7.2.1 installed side by side (`ls /opt/rocm-*`), and the
oldest one lacks CMake config files entirely, so this silently read as
"ROCm too old / HIP unavailable" for apps with real GPU/HIP kernel code
(confirmed on RAJAPerf, which has RAJA HIP-backend kernel variants gated by
CMake's `ENABLE_HIP`). Fixed in `detection.py`'s `_detect_rocm`: it now
queries `module avail -t rocm` first and picks the NEWEST version found
there (loading the module is also what actually puts a working
`hipcc`/CMake-config tree on PATH, not just having the directory exist), and
the raw `/opt/rocm-*` glob fallback now sorts by parsed semantic version
descending instead of lexical ascending. Any app with real GPU/HIP code
should end up with a recent ROCm (currently up to `rocm/7.2.1` on Tuolumne),
not whatever the oldest installed version happens to be.

**Follow-on bug: `env.sh` never loaded the resolved ROCm module at all
(fixed 2026-08-05, RAJAPerf session).** `_ensure_session_env_script`
(`install.py`) writes the session's ONE canonical `env.sh` — sourced by
every later build/install/run step — from either the app's own checked-in
scripts or, for a fresh clone with none, `systems.yaml`'s static per-system
module list. Tuolumne's `systems.yaml` entry has NO `rocm/X.Y.Z` in it (most
sessions don't need one), so even after the version-selection bug above was
fixed and `session_detect` correctly resolved `hip_tracing_needed=True` +
`rocm/7.2.1`, the cached `env.sh` (created earlier in the same session,
before HIP was known to be needed, and never invalidated) still had no ROCm
module. Every later step sourcing that `env.sh` — critically
`session_install_dftracer` — ran with `DFTRACER_ENABLE_HIP_TRACING=ON` but
no `hipcc`/ROCm CMake config anywhere on `PATH`/`CMAKE_PREFIX_PATH`, which
means `find_package(rocprofiler-sdk)` fails and HIP tracing silently
compiles out (see the software-rocm skill) — a real, independent bug.
Verified directly: with `rocm/7.2.1` NOT loaded, `hipcc` isn't even on
`PATH`; with it loaded, a trivial `.hip.cpp` compiles clean. Fixed in
`_ensure_session_env_script`: after computing the module list, it now reads
the session's own `session.json` (`detection.hip_tracing_needed` +
`detection.rocm_info.module`) and appends the resolved `rocm/X.Y.Z` module
if the app genuinely needs HIP and no rocm module is already in the list.

**Correction — this was NOT what caused the `'stdlib.h' file not found`
error in this same session.** That error's actual cause turned out to be a
third, unrelated bug — see "`'stdlib.h' file not found` has a SECOND,
unrelated root cause" further below (the `CPLUS_INCLUDE_PATH`/
`C_INCLUDE_PATH` env-var poisoning of Cray clang's toolchain). Both bugs
were real and both got fixed, but don't assume a missing ROCm module is the
explanation for a `stdlib.h` error just because this paragraph is nearby —
check `cat <ws>/scripts/env.sh` for the rocm module AND check whether
`CPLUS_INCLUDE_PATH`/`C_INCLUDE_PATH` are set in the failing step's
environment; either one alone is sufficient to cause a `stdlib.h` failure on
this system, for completely different reasons. An `env.sh` missing a needed
`rocm/` module is usually because it was generated before HIP was correctly
detected and never invalidated — remove it
(`session_remove_path(run_id, 'scripts/env.sh')`) and let it regenerate.

### Environment consistency rules (ABI safety)

These rules apply whenever installing or running any Python ML/DL app on Tuolumne.
**The install procedure and the run procedure share the same first three steps — this is what guarantees a consistent library stack.**

#### Canonical Python environment setup (install AND run)

```
Step 1 — Load app modules
  Source the app's install script (or its module block) to get the exact module stack
  the app was designed for. Never guess or use a different set.
  e.g.  source /usr/share/lmod/lmod/init/bash
        module load cce/21.0.0 cray-mpich/9.1.0 rocm/7.1.1 rccl/fast-env-slows-mpi

Step 2 — Load / activate extra software (source-built HDF5, custom libs, etc.)
  Set LD_LIBRARY_PATH with session-local paths FIRST so they shadow system/anaconda versions.
  e.g.  export LD_LIBRARY_PATH="$SESSION/install/hdf5/lib:$LD_LIBRARY_PATH"

Step 3 — Activate the Python venv (shared by app + dftracer + all dependencies)
  source "$SESSION/install/bin/activate"
```

Steps 1–3 are IDENTICAL in the install script and in every run script.
This is the only way to guarantee that `ldd` of every `.so` shows the same libraries at install time and at runtime.

#### Install-only steps (after step 3)

```
Step 4 — Set CC/CXX to the correct compiler
  Simplified (2026-07-16): after modules are loaded (Step 1), just resolve the
  MPI compiler wrappers off PATH — no separate Cray-vs-generic branch needed:
    export CC=$(which mpicc)
    export CXX=$(which mpic++)
  This works on Cray too: mpicc/mpic++ resolve to the Cray wrappers (cc/CC) once
  PrgEnv/cray-mpich modules are on PATH. Only skip this (plain gcc/g++) for a
  target that truly does not link MPI at all. Do this in the SAME script/tool
  call as the module load — PATH/module state does not persist across separate
  Bash calls. See [[feedback-cc-cxx-mpi-selection]].

Step 5 — Install all app + dftracer + dependency packages with a single pip install
  One pip invocation to resolve the full dependency graph consistently.
  For packages that can't be pip-built on NFS (mpi4py), use manual wheel extraction
  + patchelf (see item 4 below). For packages requiring source build (h5py), pass
  HDF5_DIR=<session_hdf5> before the pip call.

Step 6 — Verify every C-extension .so with ldd
  After install, run ldd on key .so files:
    - h5py:    ldd <venv>/lib/python3.13/site-packages/h5py/defs.cpython-313-*.so
    - mpi4py:  ldd <venv>/lib/python3.13/site-packages/mpi4py/MPI.mpich.cpython-313-*.so
    - dftracer: ldd <venv>/lib/python3.13/site-packages/dftracer/lib64/libdftracer_core.so
    - torch:   ldd <venv>/lib/python3.13/site-packages/torch/lib/libtorch_python.so
  Each must resolve to the session-local or module-provided library, NOT system/anaconda.
  If any .so resolves to the wrong library, fix with patchelf --set-rpath or
  --replace-needed BEFORE running anything.
```

The install.sh in `<session>/annotated/scripts/` is the single source of truth for the full stack.

---

1. **Isolated app venv** — `session_configure` creates `ws/install/` venv, separate
   from the agents' own `.venv`. Never mix them.

2. **dftracer and app share one venv** — for Python/AI/ML projects, dftracer MUST
   be installed into the same venv as the app (`ws/install/`). Never create a separate
   `ws/venv/` for dftracer on Python projects — `import dftracer` must resolve from
   the app's active Python environment. `session_install_dftracer` enforces this.

3. **FUNCTION mode always, HYBRID only as fallback** — always run with
   `DFTRACER_INIT=FUNCTION` (Python API decorators + initialize_log/finalize).
   If FUNCTION produces an empty trace, fall back to `DFTRACER_INIT=HYBRID` with
   `LD_PRELOAD=<venv>/lib/.../libdftracer_preload.so`. PRELOAD-only is never used.

4. **mpi4py on Python 3.13 with cray-mpich: use manylinux wheel + patchelf + MPI4PY_MPIABI** —
   `mpi4py<4.0` can't build on Python 3.13 (old setuptools API). `mpi4py>=4.0` ships a
   manylinux wheel with ABI-specific backends (`MPI.mpich.cpython-313-*.so`,
   `MPI.openmpi.cpython-313-*.so`); auto-detection of the ABI fails on some Tuolumne
   nodes. Use this install recipe:

   ```bash
   # 1. Download wheel to project tmp/ (pip NFS rename fails; extract manually)
   pip download 'mpi4py==4.1.1' --no-deps -d "$SESSION/tmp"
   # 2. Extract with Python (avoids pip's atomic-rename NFS issue)
   python3 -c "
   import zipfile, os, stat, sys
   whl, site = sys.argv[1], sys.argv[2]
   with zipfile.ZipFile(whl) as zf:
       for m in zf.namelist():
           if (m.startswith('mpi4py') and not m.startswith('mpi4py-')) or '.dist-info' in m:
               d = os.path.join(site, m)
               os.makedirs(os.path.dirname(d), exist_ok=True)
               if not m.endswith('/'):
                   open(d,'wb').write(zf.read(m))
                   if m.endswith('.so'): os.chmod(d, 0o755)
   " "$SESSION/tmp/mpi4py-4.1.1-cp313*.whl" "$VENV/lib/python3.13/site-packages"
   # 3. Patch MPICH backend to find cray-mpich library
   patchelf --replace-needed libmpi.so.12 libmpi_cray.so \
     "$VENV/lib/python3.13/site-packages/mpi4py/MPI.mpich.cpython-313-x86_64-linux-gnu.so"
   # 4. Set ABI env var in all run scripts — auto-detect fails on tuolumne[1764+] nodes
   export MPI4PY_MPIABI=mpich
   ```

   **NEVER** use `--no-binary=mpi4py` on Python 3.13 + NFS (build succeeds but pip
   rename to NFS fails with `[Errno 2] No such file or directory` on the output `.so`).

5. **All dataset/fractal/checkpoint I/O on Lustre** — for AI/ML workloads,
   ALL data directories (fractals, datasets, checkpoints, trace output) must
   target Lustre (`/p/lustre5/$USER/...`), not the NFS home filesystem.
   NFS is too slow for parallel dataset generation and training I/O.
   Always pass `--fract-base-dir`, `--base-run-dir`, `dataset_dir` as Lustre paths.

   Create the directories before the run:

   ```bash
   mkdir -p /p/lustre5/$USER/workspaces/<app>/{fractals,datasets,runs,traces}
   ```

6. **Module loads from app scripts** — `session_run_smoke_test` and
   `session_run_with_dftracer` automatically prepend the `ml`/`module load` lines
   extracted from the app's own `scripts/*.sh` and `*.job` files before running,
   ensuring the same environment the app author tested with.

7. **patchelf for SONAME mismatches** — after WCI wheel install, patch any `.so`
   still referencing `libmpi_gnu_112.so.12` → `libmpi_gnu.so.12` (see install script).

8. **h5py source-build + patchelf: always fix RPATH after install** —
   `pip install --no-binary=h5py h5py` with `HDF5_DIR=<session_hdf5>` source-builds h5py,
   but pip adds anaconda's lib dir to RPATH FIRST (it was on PATH during compilation).
   Result: anaconda's `libhdf5.so.310` is loaded at runtime instead of the session-built one.
   Immediately after the pip install, fix all h5py `.so` RPATH entries:

   ```bash
   for so in "$VENV/lib/python3.13/site-packages/h5py/"*.so; do
     patchelf --set-rpath "$SESSION/install/hdf5/lib" "$so"
   done
   ```

   Verify: `objdump -p <h5py_so> | grep RUNPATH` should show ONLY the session HDF5 path.

9. **dftracer + HDF5: patchelf dftracer libs + set DFTRACER_DISABLE_IO=1 for HDF5 workloads** —
   dftracer pip wheels link against system `libhdf5.so.103` (1.10.x) via GOTCHA hooks. When a
   session uses source-built HDF5 1.14.5 (`libhdf5.so.310`), two HDF5 instances load simultaneously.
   GOTCHA hooks inherited by forked DataLoader workers cause `RuntimeError: Not a property list class`
   in h5py. Two-step fix:

   Step A — patchelf dftracer C libraries to use session HDF5:
   ```bash
   for so in "$VENV/lib/python3.13/site-packages/dftracer/lib64/libdftracer_core.so" \
             "$VENV/lib/python3.13/site-packages/dftracer/lib64/libdftracer_preload.so"; do
     patchelf --replace-needed libhdf5.so.103 libhdf5.so.310 "$so"
     patchelf --set-rpath "$SESSION/install/hdf5/lib" "$so"
   done
   ```

   NEVER set `DFTRACER_DISABLE_IO=1` — GOTCHA interception must stay active for complete HDF5
   I/O tracing. The patchelf step above is the correct and complete fix.

10. **Library stack consistency: enforce in install.sh, verify with ldd** —
    Every session install script must build ALL C-extension packages (h5py, mpi4py, dftracer)
    against session-local libraries. After each pip build, verify with ldd:
    ```bash
    ldd "$VENV/lib/python3.13/site-packages/h5py/defs.cpython-313-x86_64-linux-gnu.so" \
      | grep -E "hdf5|mpi"
    ```
    No `/usr/lib64/libhdf5` or `/collab/...anaconda.../lib/libhdf5` should appear.
    The install.sh in `<session>/annotated/scripts/` is the single source of truth.

11. **flux proxy always uses a wrapper script** — never pass `module load` or env
   exports inline via `flux proxy <JOBID> bash -c "..."`. Always write the payload
   to `<ws>/tmp/<name>.sh` (sourcing `/usr/share/lmod/lmod/init/bash` at the top),
   then run `flux proxy <JOBID> bash <ws>/tmp/<name>.sh`. The MCP tools
   `session_run_smoke_test` and `session_run_with_dftracer` do this automatically
   via `_ensure_flux_proxy_wrapper`.

### Running PyTorch benchmarks with Flux

ScaFFold and similar apps use `torchrun-hpc`. Always write a wrapper script:

```bash
# Write ws/tmp/run_benchmark.sh:
#!/bin/bash
source /usr/share/lmod/lmod/init/bash
module load cce/21.0.0 cray-mpich/9.1.0 rocm/7.1.1 rccl/fast-env-slows-mpi
export ROCM_PATH=/opt/rocm-7.1.1
export LD_LIBRARY_PATH=/opt/cray/pe/lib64/cce:...
source <ws>/install/bin/activate
torchrun-hpc -N 1 -n 4 --gpus-per-proc 1 <ws>/install/bin/scaffold benchmark -c config.yml

# Then invoke:
flux proxy <JOBID> bash <ws>/tmp/run_benchmark.sh
```

Use `-g=1` (1 GPU per task) in flux alloc for GPU-bound jobs.

## Software / Library Discovery Rules

**NEVER use `find /usr/tce`, `find /opt/cray`, `find /opt/rh`, or similar recursive
filesystem searches to locate compilers, libraries, or tools.** These trees are very
large and will exhaust system resources or time out.

Instead, always discover software through the module system:

```bash
module avail hdf5          # find HDF5 installations
module avail cray-hdf5     # Cray-specific HDF5
module avail python        # Python versions
module avail rocm          # ROCm versions
module avail cray-mpich    # MPI variants
module spider <name>       # detailed search including dependencies
module show <module/ver>   # show paths and env vars for a specific module
```

Once a module is found, get its library and include paths from `module show`:

```bash
module show cray-hdf5/1.14.3.3
# Look for HDF5_DIR, HDF5_ROOT, CPATH, LD_LIBRARY_PATH entries in output
```

### Module Compatibility and Inactive Module Detection

After loading any module stack, **always check the output for "Inactive Modules"**:

```
Inactive Modules:
  1) cray-hdf5-parallel/1.14.3.7
```

An inactive module means it is **incompatible with the current stack** and was
silently disabled. Do NOT assume it is loaded.

**Known incompatibility on Tuolumne**: loading
`cce/21.0.0 cray-mpich/9.1.0 rocm/7.1.1 rccl/fast-env-slows-mpi`
deactivates `cray-hdf5-parallel/1.14.3.7`.

#### Rules by software type when a module goes inactive:

**HDF5 (or any data-format library)** → **source install**:
  The system HDF5 module is incompatible with the cce/cray-mpich/rocm stack.
  Build HDF5 from source into the session workspace install prefix:
  ```bash
  wget https://support.hdfgroup.org/releases/hdf5/v1_14/v1_14_5/downloads/hdf5-1.14.5.tar.gz
  tar xf hdf5-1.14.5.tar.gz && cd hdf5-1.14.5
  ./configure --prefix=<WS>/install/hdf5 --enable-shared --disable-static
  make -j8 && make install
  HDF5_DIR=<WS>/install/hdf5 pip install --no-binary=h5py h5py
  ```

**MPI or Compiler** → **NEVER source install**. Always find the correct compatible
  module combination. Strategy: load the **most constrained/dependent** software first
  and let lmod resolve the rest. Example — loading `rccl/fast-env-slows-mpi` first
  forces the correct `cce`, `cray-mpich`, and `rocm` versions automatically:
  ```bash
  module load rccl/fast-env-slows-mpi    # most constrained → forces others
  module list 2>&1 | grep -A5 "Inactive" # verify nothing went inactive
  ```
  If MPI is still inactive, use `module spider <mpi_module>` to find the required
  prerequisite chain, then load those first.

#### Detecting inactive modules in wrapper scripts

Add this guard after any `module load` block:

```bash
INACTIVE=$(module list 2>&1 | awk '/Inactive Modules/{f=1; next} f && /^$/{f=0} f{print}')
if [ -n "$INACTIVE" ]; then
  echo "ERROR: Inactive modules detected: $INACTIVE" >&2
  exit 1
fi
```

## Notes

- APU means CPU and GPU share memory — no explicit data transfer needed between host and device.
- If `module` commands fail, ensure `StdEnv` is active: `module list | grep StdEnv`.
- h5py installed via plain `pip install h5py` bundles its own HDF5 (fork-unsafe).
  On Tuolumne, `cray-hdf5` goes Inactive with the cce/cray-mpich/rocm stack, so
  `module load cray-hdf5` is not an option. Instead: build HDF5 from source and
  install h5py against it (see Software / Library Discovery Rules above). As a
  temporary workaround, set `multiprocessing_context="spawn"` on DataLoader to
  avoid fork-safety issues — but source-built HDF5 is the permanent fix.

## Build lessons (dated)
- 2026-08-04: Cray-clang (`cce/20.0.0`, clang 20.1.6) fails compiling spdlog's
  bundled/FetchContent'd `fmt` for any C++20 codebase that pulls spdlog in
  (confirmed on `llnl/ygm`):
  ```
  error: call to consteval function 'fmt::basic_format_string<...>::basic_format_string<...>'
  is not a constant expression
  ```
  This is a genuine Clang-20-stricter-consteval vs. older/bundled-`fmt`
  incompatibility, unrelated to dftracer or the app itself. **Fix: switch to
  the GNU toolchain** — `module swap PrgEnv-cray PrgEnv-gnu` (or `module load
  gcc-native/11.2` directly), then bind CC/CXX to the matching GNU cray-mpich
  wrapper dir (only `gnu/11.2` exists under
  `/opt/cray/pe/mpich/9.0.1/ofi/gnu/`, confirmed via `ls`):
  ```bash
  export CC=/opt/cray/pe/mpich/9.0.1/ofi/gnu/11.2/bin/mpicc
  export CXX=/opt/cray/pe/mpich/9.0.1/ofi/gnu/11.2/bin/mpicxx
  ```
  GCC 11.2.1 compiles the same spdlog/fmt cleanly. `rm -rf` the CMake build
  dir first — CMake caches the compiler and refuses to switch it on an
  existing `CMakeCache.txt`. If a workload needs GCC 12/13 for some other
  reason, check `/opt/cray/pe/mpich/9.0.1/ofi/gnu/` for a matching version dir
  first; only `11.2` is confirmed present as of this session.
- 2026-07-08: Fortran apps (Flash-X) FAIL to build with Cray PE `ftn`/`craycc`
  (Fortran flag incompatibilities). Use the **GNU MPI wrappers** at
  `/opt/cray/pe/mpich/9.0.1/ofi/gnu/11.2/bin/{mpif90,mpicc,mpicxx}`, and add
  `-fallow-argument-mismatch` to gfortran FFLAGS to tolerate MPI Fortran
  interface type mismatches. LD_LIBRARY_PATH must include the CCE libs +
  `/usr/lib64` at link time (dlopen). See [[workload-flashx]].

## Running under an existing allocation (lessons 2026-07-08)

- **Always use `flux proxy <alloc_id> flux run ...` when an allocation is already up.**
  A bare `flux run -N.. -n..` from a login shell does NOT run inside your existing
  allocation — it silently submits a NEW pbatch job that queues (status `S`), so the
  command appears to hang / times out. Check `flux jobs` for a stuck queued job and
  `flux cancel <jobid>` it if this happens. Find the active alloc id with `flux jobs`
  (look for your `flux` NAME job in `R` state).

- **`flux run` task env does NOT inherit your interactive `LD_LIBRARY_PATH`.** A binary
  that `ldd`-resolves fine interactively can fail under `flux run` with
  `error while loading shared libraries: libmpifort_gnu_112.so.12: cannot open shared
  object file`. → **Fix:** export the full runtime lib path INSIDE the run script/env,
  e.g. add `/opt/cray/pe/mpich/9.0.1/ofi/gnu/11.2/lib:/opt/cray/pe/lib64` (plus the
  session HDF5 `lib/` and dftracer `lib64/`) to `LD_LIBRARY_PATH`. Do not rely on
  ldd-at-build-time being sufficient at run time.

## Lustre striping for I/O optimization (L3, 2026-07-08 measured)

- Stripe the OUTPUT DIRECTORY before the app creates its first file —
  `lfs setstripe` does not affect already-created files:
  `lfs setstripe -c 16 -S 4M /p/lustre5/$USER/<app>/<run>` then verify with
  `lfs getstripe -d <dir>`. Give every optimization iteration a fresh directory.
- Cray MPICH ignores `cb_nodes` on its own; pair it with
  `CRAY_CB_NODES_MULTIPLIER` to actually raise the MPI-IO aggregator count, and
  keep `striping_unit` equal to the real stripe size. Verified: aggregators
  2 -> 16, critical-path write time 5.53 s -> 1.45 s. See [[software-mpi]] and
  [[software-hdf5]].
- Set `MPICH_MPIIO_HINTS_DISPLAY=1` to echo the hints, but do NOT trust it —
  it prints the *requested* values, not what the runtime used.
- **OST striping and `cb_nodes`/`CRAY_CB_NODES_MULTIPLIER` target write BANDWIDTH — they
  are inert for metadata-time-bound (`open()`/`stat()`-storm) shapes** (2026-07-10,
  measured on h5bench read/append/overwrite: metadata calls consumed ~97-99% of POSIX
  time, only ~2% was data transfer). `open()`/`stat()` cost lives on the Lustre MDS, not
  the OSTs an OST-stripe-count change affects — confirm with `lfs getstripe -d` and check
  whether the diagnosed bottleneck is actually bandwidth vs. metadata-time before reaching
  for this combo. `/p/lustre5`'s default Data-on-MDT PFL already covers small
  metadata-heavy files.

## dftracer HIP tracing under MPI: 4 of 5 buffer-tracing kinds are broken, PAGE_MIGRATION works (2026-07-20, refined)

`DFTRACER_ENABLE_HIP_TRACING=ON` (build flag; headers ARE present on `rocm-6.3.1` — an
earlier note claiming they're absent was stale) works correctly in a single-process (no
MPI) application, producing all 5 HIP-layer categories dftracer registers via
`rocprofiler-sdk`'s buffer-tracing services (`src/dftracer/core/function/hip/intercept.cpp`):
`HIP_RUNTIME_API`, `KERNEL_DISPATCH`, `MEMORY_COPY`, `SCRATCH_MEMORY`, `PAGE_MIGRATION` — each
`cat` value comes from a dynamic `rocprofiler_query_buffer_tracing_kind_name()` lookup per
kind, not a hardcoded string, confirming all 5 are genuinely part of the same HIP
instrumentation layer.

**Refined finding**: under real MPI/DDP PECAN runs, it is NOT that HIP tracing is entirely
dead — **`PAGE_MIGRATION` DOES fire (240 real events in a 16-rank run)**, while the other
FOUR kinds (`HIP_RUNTIME_API`/`KERNEL_DISPATCH`/`MEMORY_COPY`/`SCRATCH_MEMORY`) produce ZERO
events. `PAGE_MIGRATION` events originate from the KFD (kernel driver) unified-memory
page-fault/migration path — a different registration mechanism than the other four, which
depend on intercepting the user-space HIP-runtime API. This is consistent with (but doesn't
fully prove) the registration-timing-race theory: MPI initialization (confirmed via 3
isolated repros — single-process works, bare MPI/no-DDP breaks it, full DDP breaks it the
same way) most likely races only the HIP-runtime-API hook's rocprofiler-sdk registration
window (plausibly via Cray MPICH's GTL touching the HIP runtime during `MPI_Init`), while
the kernel-driver-level page-migration instrumentation registers through an apparently
unaffected path. Not something an application session can fix (would need a dftracer core
patch to register the HIP-runtime-API hook before `MPI_Init`, or an MPICH GTL init-order
change) — but this is a much more precise "which of the 5 HIP buffer-tracing kinds actually
work under MPI" starting point for a future investigation than "HIP is entirely broken."

**Workaround for GPU-kernel-level visibility on any MPI/DDP PyTorch workload on Tuolumne:
use the PyTorch Profiler integration instead** (`DFTRACER_TORCH_PROFILE`-style env-gated
`torch.profiler.profile(..., on_trace_ready=dftracer.python.torch.trace_handler)` around the
training step, logs to `cat="PP"`) — this does not depend on rocprofiler-sdk registration
timing and works cleanly under DDP (131,767 real events in the same `baseline5` run).

## Permissions

This skill uses:

- **MCP:** `mcp__dftracer__session_configure`, `session_detect`, `session_install_dftracer`, `session_run_smoke_test`, `session_run_with_dftracer`
- **Bash (in `workspaces/<session>/...` only):** `flux`, `srun`, `mpirun`, `torchrun-hpc`, `cmake`, `make`, `module`, `pip`, `patchelf`, `ldd`
- **Write / Edit:** `workspaces/<session>/*` (traces → `workspaces/<session>/traces/`, never Lustre)

Never `sudo`; never search or write under `/opt/cray`; never write outside the project root.

## `scripts/env.sh` overwrites LD_LIBRARY_PATH after module load — must append, not replace (2026-07-24)

**Symptom:** a binary built and linked successfully against cray-mpich fails at
run time with `error while loading shared libraries: libmpi_cray.so.12: cannot
open shared object file` or `libpmi.so.0: cannot open shared object file`,
even though `module load cray-mpich/9.0.1` was run just before.

**Root cause:** the session's generated `scripts/env.sh` (from
`session_detect`/`session_configure`) does `module load ...` and THEN does
`export LD_LIBRARY_PATH=<CCE paths>:/usr/lib64:...` with no `:$LD_LIBRARY_PATH`
tail — this silently discards the `LD_LIBRARY_PATH` entries the `cray-mpich`
and `cray-pmi` modules themselves set (where `libmpi_cray.so.12`/
`libpmi.so.0` actually live), keeping only the CCE compiler runtime paths.
Any downstream step that sources `env.sh` and then runs an MPI binary breaks,
even though the module load itself succeeded.

**Fix:** any script that needs additional session-local library paths (HDF5,
dftracer, etc.) on top of `env.sh` must APPEND them to the LD_LIBRARY_PATH
`env.sh` leaves in place, never replace it wholesale:
```bash
source <ws>/scripts/env.sh
export LD_LIBRARY_PATH="<session_local_libs>:${LD_LIBRARY_PATH}"
```
This is the same class of bug as the general "MPI library path" pitfall
above (`flux run` not inheriting `LD_LIBRARY_PATH`), but happens even in a
plain single-process run/build step, purely from `env.sh`'s own overwrite.
Worth fixing at the `session_detect`/`session_configure` tool level so
`env.sh` itself appends instead of replaces.

## dftracer build on Cray PE (2026-07-09)

**Symptom:** dftracer pip install from source fails:
```
fatal error: 'stdlib.h' file not found
```
when building via session_install_dftracer after STEP 1 has resolved a newer CCE/MPI version than what session_detect originally found.

**Root cause:** `session_detect` runs once during app clone/detection with system defaults (e.g., cce/20.0.0, cray-mpich/9.0.1). But STEP 1 (session setup / module resolution) finds and resolves to newer versions (e.g., cce/21.0.0, cray-mpich/9.1.0). When pip builds dftracer, the CMake setup uses stale MPI compiler paths from the original detection, causing compiler/header mismatches.

**Fix:**
1. After STEP 1 finalizes the module stack, re-run `session_detect` with explicit mpicc/mpicxx pinning to the resolved versions:
   ```bash
   # Source the env.sh created by STEP 1 to load correct modules
   source $WS/scripts/env.sh
   
   # Re-detect with explicit paths
   session_detect(run_id=..., 
     mpicc="/opt/cray/pe/mpich/9.1.0/ofi/crayclang/20.0/bin/mpicc",
     mpicxx="/opt/cray/pe/mpich/9.1.0/ofi/crayclang/20.0/bin/mpicxx")
   ```

2. Then install dftracer into the shared venv with the corrected environment:
   ```bash
   source $WS/scripts/env.sh
   export DFTRACER_ENABLE_MPI=ON
   export MPICC="/opt/cray/pe/mpich/9.1.0/ofi/crayclang/20.0/bin/mpicc"
   export MPICXX="/opt/cray/pe/mpich/9.1.0/ofi/crayclang/20.0/bin/mpicxx"
   export DFTRACER_ENABLE_HDF5=ON
   export HDF5_ROOT=/usr
   export DFTRACER_ENABLE_HIP_TRACING=ON
   pip install setuptools_scm pybind11
   pip install "git+ssh://git@czgitlab.llnl.gov:7999/dftracer/dftracer.git@develop"
   ```

**Important:** For Python/AI/ML apps, **dftracer MUST install into the same venv as the app** (not a separate `install/` directory). The session_install_dftracer MCP tool may create a separate environment; if so, manually install via pip into the shared venv instead.

## `'stdlib.h' file not found` has a SECOND, unrelated root cause: `CPLUS_INCLUDE_PATH`/`C_INCLUDE_PATH` env vars (fixed 2026-08-05, RAJAPerf session)

Same exact error text as the 2026-07-09 entry above, but a **completely
different root cause** — do not assume it's a stale module-version mismatch
without checking this first, since the fix for that entry will NOT resolve
this one. Isolated by direct testing (RAJAPerf session, dftracer's vendored
`cpp-logger` dependency build): setting `CPLUS_INCLUDE_PATH=/usr/include`
ALONE, with a perfectly consistent/correctly-resolved module stack and
matching mpicc/mpicxx throughout, was sufficient to reproduce
`fatal error: 'stdlib.h' file not found` on any C++ translation unit
including `<cstdlib>`/`<string>`/anything that pulls them in transitively —
removing just that one env var (leaving everything else identical) fixed it.

**Why:** Cray clang doesn't ship its own libstdc++; it auto-detects an
installed GCC toolchain to borrow C++ standard library headers from (on
Tuolumne: `/opt/rh/gcc-toolset-13`, confirmed via `mpicxx -v`, which shows
`Selected GCC installation: /opt/rh/gcc-toolset-13/...`). That toolset ships
`include/c++/13/cstdlib` but NOT its own `stdlib.h` — `cstdlib` does
`#include_next <stdlib.h>` expecting the search chain to fall through to the
real one, which Cray clang normally supplies itself via an implicit
`-internal-externc-isystem /usr/include`. Setting `CPLUS_INCLUDE_PATH` (or
`C_INCLUDE_PATH`) — even to that SAME `/usr/include` directory — gets
spliced into Clang's internal include-resolution chain ahead of that
implicit fallback and breaks the `#include_next` chain, so `stdlib.h`
resolves nowhere. This is NOT specific to HDF5 or any particular library;
ANY code that sets these two env vars to point the compiler at extra headers
will take down the ENTIRE build on Cray clang, including totally unrelated
dependencies that never reference the extra headers at all (confirmed: it
broke dftracer's vendored `cpp-logger`, which has nothing to do with HDF5).

**Fix:** never use `C_INCLUDE_PATH`/`CPLUS_INCLUDE_PATH` env vars to point a
Cray-clang build at extra headers. Use `-I<dir>` via `CFLAGS`/`CXXFLAGS`
instead — verified directly to achieve the identical "prefer this include
dir" goal without touching Clang's internal system-header chain. Fixed at
the tool level in `_install_dftracer_pip_direct` (install.py)'s HDF5-prefix
handling, which used to export `C_INCLUDE_PATH`/`CPLUS_INCLUDE_PATH=<hdf5
include dir>` and now appends `-I<hdf5 include dir>` to `CFLAGS`/`CXXFLAGS`
instead. If you ever hand-roll a similar "point the compiler at this extra
include dir" env setup on this system, use the flag form, not the env-var
form.

## Environment consistency (MANDATORY, applies to every step)

The application defines the environment, not the site defaults. Before touching modules,
compilers, or a venv, read the app's own scripts and reuse them VERBATIM:
`<app>/scripts/install-<system>.sh`, `<app>/scripts/<app>-<system>.job`, `pyproject.toml`.

- **install env == run env.** Same python, modules, `LD_PRELOAD`, `LD_LIBRARY_PATH`, patchelf steps.
- **Install dftracer in the SAME script and venv as the app** (critical for DL workloads,
  whose torch/mpi4py wheels pin an exact MPI/ROCm/Python ABI).
- **Bind `CC`/`CXX` to the MPI the app uses.** `which mpicc` may be the wrong wrapper; linking
  dftracer against a different MPI than the app preloads aborts at exit (`double free`).
- Pass MPI (and HDF5 only if the app uses it) explicitly to dftracer via ENV VARS.
- A zero exit code does not mean tracing worked. Verify `python -c "import dftracer.dftracer"`
  and that a NON-EMPTY `.pfw` was produced.

See the `dftracer-install` skill, RULE 0-5.

## APU core affinity + pinned memory (MI300A)

Tuolumne's MI300A is an **APU**: CPU and GPU share the same die and the same HBM. There is no
discrete host-to-device copy over PCIe.

**Set each rank's CPU affinity to ALL the cores belonging to its GPU's die.** Do not leave the
default 1-core-per-rank binding — the dataloader worker threads and the `pin_memory` copy thread
need those cores, and on an APU they are physically local to that GPU's memory.

`pin_memory=True` only pays off **when affinity is set correctly**. Pinned (page-locked) staging
lets the copy engine run asynchronously; if the rank is pinned to one core, the pinning thread
contends with the worker threads and the benefit inverts. Treat the two as ONE change:
`pin_memory=True` + full-die affinity per rank. Measure them together.

Rule of thumb for a 4-GPU node: `cores_per_rank = total_cores / gpus_per_node`, and bind rank i
to the core range of GPU i's die (verify with `flux run --verbose` / `hwloc-bind --get`, or
`rocm-smi --showtopo` for the die-to-core map).

## The four optimization axes to sweep (in this order)

1. **Overlap of compute and I/O.** Prefetch workers, `persistent_workers`, `prefetch_factor`,
   async checkpointing. Cheapest and usually the largest win.
2. **File layout / access pattern.** Minimize the *number* of reads and metadata calls. Many
   small `.npy` files means an `open`/`stat`/`close` storm on the MDS. Shard/aggregate into few
   large files with an index; prefer streaming reads over per-sample opens.
3. **System utilization.** Parallel-filesystem bandwidth (striping, Data-on-MDT for small files)
   and memory bandwidth (roofline: is the kernel bandwidth-bound or compute-bound?). On an APU,
   HBM bandwidth is shared by CPU and GPU — a CPU-side dataloader steals GPU bandwidth.
4. **Compute.** Mixed precision / `torch_amp`, kernel selection (MIOpen tuning), and only then
   algorithmic change.

Always check a wall-clock win against event/byte counts first: reducing checkpoint frequency or
epochs is *doing less*, not going faster.

### MEASURED: affinity had no effect on ScaFFold (and how we nearly got it wrong)

The APU reasoning above is sound, but on ScaFFold (32 ranks, MI300A) it produced **no measurable
change**, tested as two separate halves against a CONCURRENT control:

| change | train time delta (paired) |
| --- | --- |
| `torchrun-hpc -p cores_per_node=96 gpus_per_node=4` (24 cores/die) | +0.4% |
| `OMP_NUM_THREADS=6 OMP_PROC_BIND=close OMP_PLACES=cores` | +1.8% |

Both within noise. Reason: PyTorch's `pin_memory=True` was already set, and `torchrun-hpc`'s
default binding was already adequate — there was no headroom to recover.

**The trap.** Bundling both changes and comparing against a baseline from an hour earlier showed a
**+22.5% regression** that does not exist. Two runs of the *identical* control config 30 minutes
apart measured `train=140.96 s` and `train=293.46 s` — a 2x swing from cluster contention alone.

Rules this cost us:
- Change ONE thing per run, or you cannot attribute the result.
- Always run the control CONCURRENTLY on a separate same-size allocation. See
  [[dftracer-optimization-kb]].
- Before proposing affinity work, check whether `pin_memory` is already enabled and whether the
  launcher already binds sensibly (`hwloc-bind --get`, `flux run --verbose`).

### Launcher-level CPU affinity has no measurable effect — confirmed three times

`flux cpu-affinity` (launcher-level pinning) showed no measurable effect across THREE different
workloads on Tuolumne MI300A: the ScaFFold PyTorch DDP run above, a vpic-kokkos Kokkos-OpenMP
run (2026-07-14), and h5bench MPI-IO cpu-affinity (−0.5%, negligible). A fourth workload,
PECAN (PyTorch+PyTorch-Geometric DDP, 2026-07-20), inferred the same conclusion from prior KB
without re-testing. Do not propose launcher-level affinity tuning as an optimization lever on
this system for ANY workload class tested so far (PyTorch DDP, Kokkos-OpenMP, MPI-IO) — if
thread placement matters, tune it INSIDE the process instead (`OMP_PLACES`/`OMP_PROC_BIND` for
OpenMP, or the equivalent runtime-level affinity API for the framework in use), not via the job
launcher. Do not re-test launcher-level affinity without a fundamentally different mechanism
(e.g. explicit NUMA-domain buffer allocation, not launcher pinning) — the lever itself, not the
workload, is what's inert.

### MI300A unified CPU+GPU HBM makes pinned-memory/cross-NUMA levers structurally inert

On MI300A, CPU and GPU share the same HBM memory domain (APU architecture — no discrete device
memory, no explicit host-device transfer). This means pinned-host-memory optimizations and
cross-NUMA-node placement levers that matter on discrete-GPU systems have no analogous headroom
here — don't propose them as optimizations on this system. NUMA-aware allocation *within* the
unified domain could still matter; it just isn't the same lever as classic pinned-memory/NUMA
tuning.

### Lustre client readahead is already at max — don't propose tuning it

`/p/lustre5` client-side readahead (`llite.*.max_read_ahead_mb`) is already set to 512 MB per
client on Tuolumne compute nodes. `max_read_ahead_per_file_mb` is admin-only (`lctl set_param`
returns Permission denied for unprivileged users). Don't propose user-space readahead tuning as
an L3 optimization here — there is no headroom to gain and no permission to change it anyway.

### Check `stat -f <venv>` before crediting an I/O finding on a Python-heavy multi-node workload

Confirmed on ray_molformer (2026-08-02, 4-node/16-GPU): a session's Python virtualenv living
under `/usr/WS2/...` resolves to NFS (`stat -f` → `Type: nfs`), not Lustre, even though
`dataset/`-style symlinks correctly point at `/p/lustre5`. A venv on NFS causes a large POSIX
metadata-op storm from every Ray/multi-process worker's `import` machinery walking
`site-packages` independently (measured: 81.3% of total I/O time, 691K ops) — always
`stat -f` the venv path before attributing an I/O bottleneck finding, since the fix (stage the
venv to node-local storage or Lustre) is completely different from an app-data I/O fix. In
this case the venv-on-NFS traffic was bounded to <=5.8% of the actual (compute-side)
bottleneck, so it did not warrant action — but the bound has to be measured, not assumed.

### PyTorch's ROCm build bundles its own ROCm libraries and ignores `/opt/rocm`

Confirmed on ray_molformer (2026-08-02): a PyTorch-ROCm wheel installed into a session venv
ships its own copies of `librocblas.so`, `librocsolver.so`, `libMIOpen.so`, `librocsparse.so`,
`libtorch_hip.so`, `librccl.so` (~9.7GB total) under `<venv>/lib/python*/site-packages/torch/
lib/`, and the running process loads THESE, not the system `/opt/rocm` module's libraries —
confirmed via `ldd`/loaded-library inspection, not assumption. An optimization attempt that
prewarms/stages `/opt/rocm` libraries (e.g. to cut GPU code-object load time) will silently
target the wrong files and measure a null result. Before proposing any ROCm-library
prewarm/staging/caching optimization, verify which library files the actual process loads
(check `torch/lib/*.so` first, not the module-loaded system ROCm tree).

## Cray-clang 20.1.6 breaks on spdlog's bundled/fetched fmt (consteval error) — use PrgEnv-gnu instead

**Symptom:** a C++20 project that FetchContent-pulls spdlog (which bundles its
own `fmt`) fails to compile under Cray-clang 20.1.6 (`PrgEnv-cray`/`cce/20.0.0`)
with errors like:

```
.../spdlog-src/include/spdlog/logger-inl.h:139:13: error: call to consteval function
'fmt::basic_format_string<...>::basic_format_string<FMT_COMPILE_STRING, 0>' is not a constant expression
```

**Root cause:** Clang 20 enforces `consteval` constant-expression rules more
strictly than older/bundled `fmt` releases expect. This is a genuine
clang20/bundled-fmt incompatibility — unrelated to dftracer, MPI, or any
project-specific code. Confirmed on `llnl/ygm` (2026-08-04).

**Fix:** switch to the GNU toolchain instead of fighting Clang 20's checker.
Tuolumne's `cray-mpich` module has a GNU-built wrapper directory:

```bash
module swap PrgEnv-cray PrgEnv-gnu
module load gcc-native/11.2        # matches the available cray-mpich gnu/11.2 wrapper dir
export CC=/opt/cray/pe/mpich/9.0.1/ofi/gnu/11.2/bin/mpicc
export CXX=/opt/cray/pe/mpich/9.0.1/ofi/gnu/11.2/bin/mpicxx
```

GCC 11.2.1 compiles the same spdlog/fmt code cleanly. Note: only `gnu/11.2` has
a matching `cray-mpich` wrapper directory under
`/opt/cray/pe/mpich/9.0.1/ofi/gnu/` at the time of writing — newer GCC modules
(`gcc-native/12`, `12.1`, `12.2`, `13`, `13.2`) are available on Tuolumne but do
NOT have a matching pre-built cray-mpich gnu wrapper dir; using one of those
would need a different MPI-linking mechanism (verify with
`ls /opt/cray/pe/mpich/9.0.1/ofi/gnu/` before assuming a version is available).
Always `rm -rf` the build directory before switching compilers on an existing
CMake build tree — CMakeCache.txt pins the compiler and CMake refuses to
silently swap it.

See [[dftracer-annotation-lessons]] LESSONS_LOG.md (2026-08-04, YGM session)
for the full build log and the other unrelated fixes made in the same session.

## HSA_ENABLE_INTERRUPT=0 (busy-poll) is a NET REGRESSION for mixed CPU+GPU apps on MI300A (2026-08-05)

- **Symptom:** a GPU phase dominated by many blocking `hipStreamSynchronize` round-trips
  looks like it should benefit from HSA busy-wait completion signals, and a single-replicate
  probe appeared to confirm it (-9.9% / -16.4% on two sync-heavy kernels).
- **Root cause:** `HSA_ENABLE_INTERRUPT=0` makes the host thread spin-wait for completion
  signals, burning one host core per rank. On MI300A (unified APU, host cores shared with
  the ranks' own CPU work) that CPU is taken away from any co-resident host-side compute.
- **Measured (RAJAPerf, 4 nodes x 16 ranks, 5 interleaved replicates, untraced both arms):**
  whole-suite p50 **+3.78% (worse)**; CPU-only `Base_Seq` kernels **+3.95% (worse)**;
  GPU `Base_HIP` kernels only -1.03%, with overlapping ranges.
- **Exact guidance:** do not use `HSA_ENABLE_INTERRUPT=0` on Tuolumne unless the phase is
  pure-GPU with genuinely idle host cores. The single-replicate probe result did NOT
  replicate over 5 interleaved replicates - treat it as a worked example of why a
  single-sample delta is never creditable here.
- `rocm-smi --setperflevel high` remains admin-only on Tuolumne (no sudo) - not a usable
  L3 lever from inside a `flux run`.

## PAPI: pin `papi/7.2.0.2`, NEVER the newest `papi/7.3.0.1` (2026-08-12, measured)

`papi/7.3.0.1` — the newest available and what a "load the latest module" habit
picks — **SIGSEGVs at `PAPI_library_init`** on this system. It ships a `rocp_sdk`
component that `dlopen()`s `librocprofiler-sdk.so`, and it was built against a
different rocprofiler-sdk than ROCm 6.4.2 provides (0.6.0):

```
E... agent.cpp:1226] size of rocprofiler agent struct used by caller is
     ABI-incompatible with rocprofiler_agent_v0_t in rocprofiler
Segmentation fault (core dumped)
```

This kills **any** process that initialises that PAPI, so it breaks both
dftracer's build-time counter probe *and* the traced application at run time.

Measured with dftracer's own `cmake/probes/papi_probe.c` on an MI300A node:

| module | result |
| --- | --- |
| `papi/7.3.0.1` | **SIGSEGV** in `PAPI_library_init` |
| **`papi/7.2.0.2`** (module default) | **30 presets**, 5 hw slots / 7 fitting |
| `papi/7.1.0.4` | 30 presets |
| `papi/7.0.1.2` | 19 presets |
| `papi/6.0.0.16` | 23 presets |

So `7.2.0.2` is both safe and the richest set — and it *does* include cache
counters (`PAPI_L1_DCM`, `PAPI_L2_DCM/ICM/TCM`, `PAPI_L1_DCA`, TLB, branch,
`PAPI_FP_OPS`/FMA/vector). Any documentation claiming MI300A exposes no cache
presets was measured on an older/different PAPI.

Two further PAPI notes on this site:

- **Load `papi` LAST, on its own `module load` line.** `rocmcc` auto-replaces
  `cce/20.0.0` and triggers a MODULEPATH change that reloads
  `cray-libsci`/`cray-mpich`; loading `papi` in the *same* command as those gets
  it silently dropped from the final module list, leaving `CRAY_PAPI_PREFIX`
  empty with no error.
- **The modulefile does not export `PAPI_DIR`**, only `CRAY_PAPI_PREFIX` (plus a
  `pkgconfig` dir). dftracer's `FindPAPI.cmake` reads `PAPI_DIR` from the
  environment, and the distro ships an ancient PAPI 5.6
  (`/usr/include/papi.h` + `/lib64/libpapi.so.5`) that `find_path`/`find_library`
  will otherwise resolve to — mixing a 5.x header with a 7.x runtime. Always
  `export PAPI_DIR="${CRAY_PAPI_PREFIX}"`.

## GPU profiling: `SYS_PERFMON` is denied, but kernel tracing still works (2026-08-12)

Every ROCm-profiling process on these nodes logs, once per GPU:

```
W... ioctl.cpp:68] Device NNNNN could not be locked for profiling due to lack of
     permissions (capability SYS_PERFMON). PMC Counters may be inaccurate and
     System Counter Collection will be degraded.
```

This is a *warning*, and it is NOT a blanket denial of GPU profiling —
`rocprofv3 --kernel-trace` on the same node captured 9,392 kernel rows fine. It
degrades **PMC hardware counters** only. So do not conclude "GPU profiling is
blocked here" from this message; test with `rocprofv3 --kernel-trace` before
blaming the site.

Consequence for cmake probes: `try_run()` merges stdout and stderr, so these
warnings land in the captured probe output and corrupt any *positional* parsing
of it. See [[workload-laghos]] and the dftracer PAPI-probe fix.

## dftracer GPU tracing: `force_configure` cannot win the registration race (2026-08-13, FIXED)

**Supersedes the "4 of 5 buffer-tracing kinds are broken under MPI, PAGE_MIGRATION
works" section above.** That conclusion was wrong about the mechanism. The kinds
were never individually broken and MPI/KFD was never the distinction — dftracer
simply never got registered as a rocprofiler client at all.

`HIPFunction::initialize()` registered via `rocprofiler_force_configure()` and
**discarded the status**. On Cray PE + ROCm the load-time constructors of
`librocprofiler-register` / `libamdhip64` / the Cray MPICH GTL bring rocprofiler up
*before `main()` is entered*, so configuration is already locked by the time any
application code runs — including `DFTRACER_CPP_INIT` as the literal first
statement of `main`. The call returned
`ROCPROFILER_STATUS_ERROR_CONFIGURATION_LOCKED (16)` on every run and GPU tracing
silently collected nothing.

Fix (in dftracer, `src/dftracer/core/function/hip/intercept.cpp`): export the
supported global `rocprofiler_configure` symbol, which rocprofiler discovers by
scanning loaded libraries and calls at the correct point in its own init. It
returns `nullptr` unless `DFTRACER_ENABLE` is set, so merely linking dftracer does
not turn a process into a profiling tool.

After the fix, ALL FIVE kinds fire. Measured on Laghos at 4 nodes x 16 GPUs:
`KERNEL_DISPATCH` 133,733, `HIP_RUNTIME_API` 46,150, `MEMORY_COPY` 32,
`PAGE_MIGRATION` 16, `SCRATCH_MEMORY` 16.

Diagnostic value: the whole thing was invisible for a full baseline because one
return value was dropped. Check `rocprofiler_force_configure`'s status — a
`CONFIGURATION_LOCKED` reading names both cause and fix immediately.

## Reserve a core for `dftracer_service`, or Flux hangs the job forever (2026-08-13)

An MI300A node has 96 cores. Requesting `-c 24` for 16 ranks over 4 nodes uses
every core — and the `dftracer_service` daemon already holds 1 core per node. Flux
then leaves the application job in state **`S` (pending) indefinitely**, waiting
for cores the daemon holds. It does not fail, error, or time out; the run just
never starts. Use `-c 23` (92 of 96 cores) so the daemon's core is available.

Also: a detached `dftracer_service` **survives `flux cancel`** of the job that
launched it. Its pid file persists and the next `start` refuses with
"dftracer_service is already running on <host> (PID N) ... refusing to start a
second instance", so the run proceeds with NO node counters. Always
`dftracer_service stop <state_dir>` before `start`. The pid files live **flat** in
the state dir as `dftracer_server_<hostname>.pid`, NOT in per-host subdirectories
— a readiness poll globbing `<state_dir>/*/…` finds nothing and falls through.

## Pin `dftracer_service` start / app / stop to the SAME hosts inside a shared allocation (2026-08-13)

In an allocation larger than the job you are running, three separate
`flux submit -N4` calls get three **different** 4-node subsets. Observed inside a
32-node allocation while running a 4-node job:

- `dftracer_service start` landed on `<nodeA>,<nodeB>,<nodeC>,<nodeD>`
- the application landed on a different 4 nodes
- `dftracer_service stop` landed on a third set

Consequences, all silent: the daemons profiled nodes the application never ran on;
`stop` reported `No running server found.` for nodes whose daemons it had not
started; the un-stopped daemons kept running as orphans holding a core each; and
their `service_<host>.pfw.gz` files stayed **0 bytes**, because the trace is only
flushed on stop. Nothing errors — you get a complete-looking run with four empty
node-counter traces.

Pin every one of the three jobs to the same explicit hosts:

```bash
HOSTS=<nodeA>,<nodeB>,<nodeC>,<nodeD>
REQ="--requires=host:$HOSTS"
flux submit -N4 -n4 -c1 $REQ --setattr=exclusive=false "$DFT_BIN" start "$SVC_DIR"
flux run    -N4 -n16 -g1 -c23 $REQ --setattr=exclusive=false ./app_launch.sh
flux submit -N4 -n4 -c1 $REQ --setattr=exclusive=false "$DFT_BIN" stop  "$SVC_DIR"
```

Pick the hosts by first listing what is already busy — a shared allocation may be
carrying someone else's job, and `flux resource list` shows the *cluster*, not the
allocation's internal occupancy:

```bash
flux jobs -a --format="{id} {name} {status} {nnodes} {nodelist}" | grep RUN
```

Verifying afterwards: every `service_<host>.pfw.gz` must be non-empty and there
must be exactly one per node in the job. A 0-byte service trace means a daemon was
never stopped — find it with `ls $SVC_DIR/dftracer_server_*.pid` and stop it on its
own host with `flux run -N1 -n1 -c1 --requires=host:<h> ... stop $SVC_DIR`.

## `feature/papi-counter-tracing` is missing the PAPI-sampler teardown fix (2026-08-13)

On `feature/papi-counter-tracing` with `DFTRACER_ENABLE_PAPI_TRACING=1`, 1-3 ranks
of 16 segfault **after the application has finished all its work** — the science
completes and prints correct results, then teardown crashes and those ranks' traces
never flush. A different rank each run, so it is a race, not a fixed offender.

Isolated by A/B on identical pinned hosts with identical arguments (miniFE
`nx=768`, 4 nodes x 4 ranks):

| Configuration | Result |
| --- | --- |
| annotated, PAPI on | SIGSEGV — 13/16, 14/16, 15/16 ranks flushed across three runs |
| annotated, `DFTRACER_ENABLE_PAPI_TRACING=0` | clean, **16/16** |
| pristine un-annotated binary | clean |
| PAPI on, `DFTRACER_PAPI_EVENTS` cut to 5 fitting counters | still SIGSEGV (15/16) |

So it is neither the annotation nor counter multiplexing. It is duration-dependent:
the same binary and counter set at `nx=256` (17 s, 16 ranks) was clean at 16/16,
while ~3-4 minute runs crash.

**This is a KNOWN, already-root-caused bug -- not a new one.** `DFTracerCore::finalize()`
stops the PAPI sampler AFTER `posix_instance->unbind()/finalize()`. The sampler runs on its
own libuv timer thread and calls `PAPI_read()` each tick; PAPI reads its perf_event fd with
`read(2)`, which dftracer's own brahma/GOTCHA wrapper intercepts, so the next sample dies in
`gotcha_get_wrappee()`. Full backtrace and the fix (finalize the sampler BEFORE releasing
the I/O bindings) are in the memory entry
`bug-dftracer-rocprofiler-configure-race-and-papi-sampler-segv`.

The fix simply **has not landed on `feature/papi-counter-tracing`**. A fresh install from
that branch demonstrably carries the *other* fix from the same work -- `nm -D
--defined-only libdftracer_core.so | grep rocprofiler_configure` shows the symbol exported
and GPU tracing works -- while still reproducing this crash. Check the ordering in
`DFTracerCore::finalize()` on whatever branch you install rather than assuming that PAPI
support implies the fix.

Workaround, pick per session: keep PAPI on and accept losing a few ranks' traces
(the survivors are complete, and the run's science is unaffected), or set
`DFTRACER_ENABLE_PAPI_TRACING=0` for a guaranteed full-rank trace with no hardware
counters. **Always count zero-byte `*-app.pfw.gz` files after a PAPI run** — that
count is the number of ranks you lost.

## Two run-wrapper traps that fail SILENTLY (2026-08-13)

**`GROUPS` is a special bash variable.** It holds the current user's group IDs
(`id -G`). Assigning `GROUPS=("a|1" "b|2")` is **silently ignored** — no error,
no warning — and a `for x in "${GROUPS[@]}"` loop then iterates over your GIDs.
A run matrix built that way skips every entry and reports success:

```
DBG entry=[35619] NAME=[35619]     # these are GIDs, not run names
```

Name run-matrix arrays anything else (`PAPI_GROUPS`, `RUN_GROUPS`, ...). Other
bash specials to avoid for the same reason: `SECONDS`, `LINENO`, `RANDOM`,
`PIPESTATUS`, `BASH_*`, `UID`, `EUID`, `HOSTNAME`, `PWD`, `OLDPWD`.

**The allocation may not be yours alone.** A 32-node allocation was carrying
another session's `_sweep_laghos` job plus **12.5-hour-old orphaned
`dftracer_service` daemons** on three of the four nodes that had been picked for
pinning. `flux resource list` shows the *cluster*, not the allocation's internal
occupancy. Before pinning hosts, list what is actually running inside it and pick
free nodes:

```bash
flux proxy <alloc> flux jobs -a --format="{id} {name} {status} {nnodes} {nodelist}"
flux proxy <alloc> flux jobs --filter=running -no "{nodelist}"   # subtract these
```

Orphaned daemons matter beyond stolen cores: they keep appending to
`service_<host>.pfw.gz`, so a later run's node counters get mixed with hours of
someone else's. That is how a service trace ends up spanning ~14 days. See
[[bug-dftracer-service-hosts-must-be-pinned]].

## Two `librocm_smi64` ABIs in one process = heap corruption at exit (2026-08-13, FIXED)

Symptom: the app finishes all its work, prints correct results, then dies with

```
corrupted size vs. prev_size in fastbins
Aborted (core dumped)          # exit 134
```

Deterministic, reproducible at 1 rank / `nx=64` in seconds, and **independent of every
runtime flag** — it persisted with `DFTRACER_ENABLE_PAPI_TRACING=0` and with
`DFTRACER_DISABLE_VARIORUM_POWER=1`, which is the tell: the cause is *linked*, not
*executed*.

Root cause: two different ABIs of the same library loaded simultaneously.

```
librocm_smi64.so.1 => /usr/lib64/hwloc/librocm_smi64.so.1     # hwloc's bundled copy
librocm_smi64.so.7 => /opt/rocm-6.4.2/lib/librocm_smi64.so.7  # pulled in by variorum
```

Different SONAMEs, so the loader maps **both**, and they export **374 identical symbol
names**. ELF interposition then routes every call into whichever copy is first in the
lookup scope, while each library keeps its own internal state — so a structure allocated
under one version's layout gets written or freed under the other's. That is textbook
fastbins corruption.

Variorum needs `librocm_smi64` for its AMD GPU power domain. As long as variorum is linked
into `libdftracer_core` — which every application rank links — every traced app inherits
the conflict.

**Fix:** link variorum only into the service that actually calls it
(`dftracer_service` / `_service_telemetry`), never into the core. Verify:

```bash
ldd <prefix>/lib64/libdftracer_core.so | grep -c rocm_smi   # must be 0 or 1, never 2
ldd <app-binary>                       | grep -c rocm_smi   # must be 1
```

Note the service binary legitimately still links both (it needs variorum), so the same
hazard remains *inside the service process* — watch for it if service traces ever come
back short.

**Generalises:** any time `ldd` shows the same library name at two different SONAME
versions, treat it as a live heap-corruption risk, not cosmetic. `nm -D --defined-only`
on both and comparing exported symbols tells you in seconds whether they can collide.
