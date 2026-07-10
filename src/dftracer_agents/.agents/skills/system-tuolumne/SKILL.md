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
- Python: `python/3.13.2`

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

## I/O and Workspace

- **All benchmark I/O must target Lustre**: use `/p/lustre5/$USER/workspaces/ior/` as the data directory for IOR runs and trace output. This path is read/write accessible and is the correct path for high-bandwidth parallel I/O workloads.
- Smoke tests and benchmark runs must pass `DATA_DIR=/p/lustre5/$USER/workspaces/ior` (or the equivalent `--output-file` / `-o` flag for IOR) so that files land on Lustre, not on the shared home filesystem.
- Use `workspaces/<session>/tmp/` inside the project directory for build artifacts and temporary files.
- Never write to `/tmp` directly.

```bash
# Create Lustre workdir before any IOR run
mkdir -p /p/lustre5/$USER/workspaces/ior
```

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
patterns to find the ROCm version — no need to have the module pre-loaded.
`DFTRACER_ENABLE_HIP_TRACING=ON` is set automatically when ROCm is found.

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
  If MPI is in the stack: export CC=cc CXX=CC   (Cray MPI wrapper)
  Otherwise:              export CC=gcc CXX=g++

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

## Permissions

This skill uses:

- **MCP:** `mcp__dftracer__session_configure`, `session_detect`, `session_install_dftracer`, `session_run_smoke_test`, `session_run_with_dftracer`
- **Bash (in `workspaces/<session>/...` only):** `flux`, `srun`, `mpirun`, `torchrun-hpc`, `cmake`, `make`, `module`, `pip`, `patchelf`, `ldd`
- **Write / Edit:** `workspaces/<session>/*` (traces → `workspaces/<session>/traces/`, never Lustre)

Never `sudo`; never search or write under `/opt/cray`; never write outside the project root.

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
   pip install "git+https://github.com/llnl/dftracer.git@develop"
   ```

**Important:** For Python/AI/ML apps, **dftracer MUST install into the same venv as the app** (not a separate `install/` directory). The session_install_dftracer MCP tool may create a separate environment; if so, manually install via pip into the shared venv instead.


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

### Lustre client readahead is already at max — don't propose tuning it

`/p/lustre5` client-side readahead (`llite.*.max_read_ahead_mb`) is already set to 512 MB per
client on Tuolumne compute nodes. `max_read_ahead_per_file_mb` is admin-only (`lctl set_param`
returns Permission denied for unprivileged users). Don't propose user-space readahead tuning as
an L3 optimization here — there is no headroom to gain and no permission to change it anyway.
