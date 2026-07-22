# DFTracer Pipeline Plan — pecan_milan/20260720_153336

## Overview

**App:** PECAN/MILAN (Pose Classification / Binding Affinity Prediction + Multi-Instance
Learning Atomic Network). Pure Python (PyTorch + PyTorch-Geometric) ML/DL app. Source
cloned from `$HOME/dyad_pecan/pecan_milan` (branch `main`) into session
`source/`.

**System:** tuolumne (Cray PE, AMD MI300A APU, ROCm, flux run launcher, no sudo). Base
modules from `system_detect`: craype-x86-trento, libfabric/match_SHS, craype-network-ofi,
perftools-base/25.09.0, craype/2.7.35, PrgEnv-cray/8.7.0, flux_wrappers/0.1, xpmem/2.6.5,
cce/20.0.0, cray-libsci/25.09.0, cray-mpich/9.0.1, python/3.13.2. Must additionally set
`LD_LIBRARY_PATH` to include `/opt/cray/pe/cce/20.0.0/cce/x86_64/lib`,
`/opt/cray/pe/cce/20.0.0/cce/x86_64/lib/default64`, `/usr/lib64` (for `dlopen`) BEFORE
calling `session_install_dftracer` / `session_build_annotated` (those run in a separate
process, exports made in a Bash tool call do not propagate).

**Session paths (canonical, from `session_status` / `session_get_run_paths`):**
- workspace: `$WORKSPACE_ROOT`
- source: `<WS>/source`
- annotated: `<WS>/annotated`
- baseline run dir: `<WS>/baseline` (traces_raw=`<WS>/baseline/traces/raw`,
  traces_compact=`<WS>/baseline/traces/compact`, scripts=`<WS>/baseline/scripts`)
- dataset: `<WS>/dataset` — SYMLINKED to `$VAST_ROOT/data_pecan` (resolved, see
  STEP 1 amendment below)
- artifacts: `<WS>/artifacts` (ALL logs go here)
- performance: `<WS>/performance` (profile already bound: parent MLflow run
  `5e8e9a<flux-jobid>ddadc94a9b80e`)

**Key facts already discovered (do not re-derive):**

1. **`dftracer_logger.py` at repo root is a REAL, ALREADY-WORKING dftracer integration
   wrapper**, not a stub — it wraps `dftracer.python.dftracer` / `dft_fn` behind
   `DFTRACER_ENABLE=1`, degrading to a no-op `Dft_NullContext` otherwise. It is already
   imported and used with `dft_event_logging(...)` context managers throughout
   `pecan/dataset.py`, `pecan/trainer.py`, `pecan/datacopy_dyad.py`,
   `pecan/dataset_dyad.py`, and `main_app.py` calls `init_dftracer()`/`finalize_dftracer()`.
   `milan/*.py` has NO dftracer annotation — irrelevant to this run since we're using
   the PECAN/PDBspheres path, not MILAN, but keep the fact recorded. Historical proof
   tracing worked: a prior real trace run exists at
   `$HOME/dyad_pecan/output_log/dft_pecan_dyad_kim63_8_20250415/`
   (read-only reference, not part of session).
2. **DYAD/margo: OUT OF SCOPE for this session** (resolved by user — see STEP 1
   amendment). Use the local-I/O fallback: run `main_app.py` WITHOUT `--dyad`, which
   uses `pecan/dataset.py` instead of `pecan/dataset_dyad.py`. Skip building
   mochi-margo/spack entirely.
3. **PFS dataset location — RESOLVED.** See STEP 1 amendment below.
4. **This app is Python-only.** All annotation routes through `dftracer-annotate-python`
   (invoked via `dftracer-annotator`), never any C/C++ annotator.
5. **Workload/config scope — RESOLVED.** User selected PDBspheres. See STEP 1 amendment.

**Run-length rule:** the traced baseline (STEP 6) and any optimization variant runs must
target a BOUNDED runtime of roughly 10–15 minutes of actual training/testing work —
use `--epochs` / the small `PDBspheres_v2_split8` shard / `--batch-size` overrides on
`main_app.py` to hit that window, not the full production run.

## STEP 1: dftracer-session-setup

**RESOLVED BY USER (2026-07-20) — do not re-ask:**
- **Workload:** PDBspheres (PECAN, not MILAN's pdbbind-MIL path). Base config:
  `yaml/pecan/config_pecan_pdbspheres_v2.yaml` (nn_type EGNN, label_type=2 binary
  classification).
- **Dataset:** already exists on VAST, user says NOT to move/copy anything.
  Real accessible data confirmed at `$VAST_ROOT/data_pecan/` (group `ice4hpc`,
  readable): `PDBspheres_v2/` (full set, many small per-structure hdf5 files),
  `PDBspheres_v2_split8/` (small 8-shard subset + `pdbspheres_all_0000000_all.csv` —
  use this shard for the bounded 10-15 min baseline/optimization runs), `PDBBind/`
  (`pdbbind2020_general_new.csv/.hdf5`, `pdbbind2020_refined_new.csv/.hdf5`,
  `pdbbind_casf2016_new.csv/.hdf5` — used as PECAN's `test_csvs`), `PECAN_models/`
  (checkpoint dir).
  The original config's `$VAST_ROOT/...` paths are NOT accessible to this session
  (permission denied) — that is a DIFFERENT copy of the same dataset owned by another
  group. Use the `$VAST_ROOT/data_pecan/...` copy instead (same data, this
  session's group has access).
  `<WS>/dataset` is symlinked directly to `$VAST_ROOT/data_pecan` (not a
  sub-path — exposes PDBspheres_v2, PDBspheres_v2_split8, PDBBind, PECAN_models all at
  once). Verified: `ls <WS>/dataset/PDBspheres_v2_split8` lists 8 hdf5 shards + csv.
- **Session-local config:** STEP 4/6 must write a session-scoped copy of
  `config_pecan_pdbspheres_v2.yaml` into `<WS>/annotated/yaml/pecan/` with:
  `train_csvs: [<WS>/dataset/PDBspheres_v2_split8/pdbspheres_all_0000000_all.csv]`,
  `test_csvs: [<WS>/dataset/PDBBind/pdbbind_casf2016_new.csv]`,
  `checkpoint.dir: <WS>/baseline/checkpoint` (never write checkpoints onto the PFS
  dataset dir — checkpoints are session output not input data). Do not edit the
  original `config_pecan_pdbspheres_v2.yaml` under `<WS>/source` — copy-then-edit only
  in `<WS>/annotated`.
- **DYAD:** not required for this scope — use the local-I/O fallback (`main_app.py`
  without `--dyad`), since the goal is annotate+trace+optimize PECAN/PDBspheres, not
  validate DYAD staging. Skip the margo/spack build entirely (removes the DYAD build
  risk item from STEP 2).

**COMPLETED (2026-07-20, dftracer-session-setup agent):**

**Modules Verified:**
- system_detect() confirmed: tuolumne, cce/20.0.0, cray-mpich/9.0.1, python/3.13.2
- LD_LIBRARY_PATH required: /opt/cray/pe/cce/20.0.0/cce/x86_64/lib, default64, /usr/lib64

**App Annotation Status:**
- milan/*.py: zero dftracer annotations (expected; PECAN path selected, out of scope)
- pecan/*.py: confirmed existing annotations in dataset.py, trainer.py
- main_app.py: confirmed init_dftracer()/finalize_dftracer() calls

**Build System:**
- No setup.py, pyproject.toml, or setup.cfg found → pure Python app, no compilation needed
- Next steps: pip install workflow for all deps (dftracer + torch + h5py + mpi4py + geo packages)

**HDF5/MPI Dependencies:**
- h5py: required (pecan/dataset.py imports), dataset is HDF5-based (PDBspheres_v2_split8 .hdf5 files)
  → STEP 2 will install dftracer with DFTRACER_ENABLE_HDF5=ON
  → STEP 3 will pip install h5py against session-built HDF5 (source 1.14.5)
- mpi4py: required (pecan/datacopy_dyad*.py imports, also torch.distributed.dist backend)
  → STEP 2 will set DFTRACER_ENABLE_MPI=ON, MPICC/MPICXX to cray-mpich/9.0.1 wrappers
  → STEP 3 will pip install mpi4py (manylinux wheel + patchelf per system-tuolumne Python 3.13 recipe)

**Artifact:** <WS>/artifacts/01_session_setup.log written with full findings.

**STEP 1 COMPLETE.** All pre-conditions for STEP 2 (dftracer-build-dftracer) verified.
Dataset/workload already resolved (see STEP 1 amendment above).


## STEP 2: dftracer-build-dftracer

**Inputs:** modules from Overview (rocm NOT required for dftracer core itself unless the
app's ROCm tracing is in scope — per `feedback-dftracer-install-rocm-mpi`, skip
ROCProfiler unless the app uses ROCm directly in traced code; PECAN training uses
ROCm via PyTorch but tracing dftracer's own HIP hooks is not required for a Python
function-mode integration — install dftracer WITHOUT `DFTRACER_ENABLE_HIP_TRACING`
unless annotation reveals direct HIP/ROCm calls in traced functions).

**Tasks:**
1. Build the canonical 6-step HPC Python env INSIDE the session workspace (do not reuse
   `$HOME/dyad_pecan/venv_rocm6_tuo`):
   (a) load modules from Overview exactly, (b) export
   `LD_LIBRARY_PATH` including the CCE lib dirs + `/usr/lib64` BEFORE any install/build
   MCP tool call, (c) create a fresh venv under `<WS>/venv` (or the path
   `session_install_dftracer` expects — check its default), (d) `export CC=$(which
   mpicc) CXX=$(which mpic++)` bound to `cray-mpich/9.0.1` (never a bare system
   compiler), (e) single `pip install` pass for dftracer + app deps together (see STEP 3
   — install BOTH dftracer and the app requirements in the SAME venv/script), (f)
   `ldd`/`python -c "import dftracer.dftracer"` verify.
2. DYAD/mochi-margo build: SKIPPED (out of scope, see STEP 1 resolution). Do not
   attempt spack/margo build for this session.
3. Use `session_install_dftracer` (MCP tool) first; only fall back to manual pip/cmake
   if the tool cannot express the ROCm+MPI combination.
4. Log to `<WS>/artifacts/02_build_dftracer.log`.

**Expected artifact:** working dftracer Python module importable from the session venv,
`features_enabled` list.

## STEP 3: dftracer-build-app

**Inputs:** same venv as STEP 2 (mandatory — do not create a second venv).

**Tasks:**
1. In the SAME venv, `pip install` the app's Python deps: `torch`/`torchvision`/
   `torchaudio`/`pytorch_triton_rocm` (ROCm 6.3.1 wheels — check python ABI compatibility
   with the session's python module; downgrade/adjust python module if the cp39 wheels
   do not match), `typing_extensions`, `sympy`, `mpi4py`, `flux-python==0.70`, `pandas`,
   `scikit-learn`, `h5py`, then the PyG extension wheels (`pytorch_scatter`,
   `pytorch_sparse`, `pytorch_spline_conv`, `pytorch_cluster`, `pytorch_geometric`) from
   `<WS>/source`'s own copy if vendored, else rebuild from source against the session's
   torch/ROCm — check whether `<WS>/source` vendors a `pyg-rocm-build/` directory; if
   not, this is app-repo-external and must be fetched or built against the resolved
   ROCm version.
2. No compiled build step for the app itself (pure Python) — smoke-import
   `main_app.py`'s top-level imports (`torch`, `torch_geometric`, `pecan.trainer`,
   `dftracer_logger`) to confirm the env is complete before annotation.
3. Log to `<WS>/artifacts/03_build_app.log`.

**Expected artifact:** clean `python -c "import torch, torch_geometric; from
pecan.trainer import PecanTrainer"` in the session venv.

## STEP 4: dftracer-annotator (routes to dftracer-annotate-python)

**Inputs:** `<WS>/source` (Python-only app — route through `dftracer-annotate-python`,
never a C/C++ annotator).

**Tasks:**
1. Copy `<WS>/source` into `<WS>/annotated` as the working copy.
2. **Scope:** `pecan/*.py` is ALREADY annotated (real `dft_event_logging` integration,
   confirmed in STEP 1) — do NOT re-annotate pecan files from scratch; instead VALIDATE
   the existing annotations are complete/correct (check `dataset.py`, `trainer.py` cover
   the hot I/O/compute loops relevant to the PDBspheres path) and only fill true gaps.
   `milan/*.py` is out of scope for this run (PECAN/PDBspheres selected, not MILAN).
3. `main_app.py` already calls `init_dftracer()`/`finalize_dftracer()` — verify.
4. Write the session-scoped config per STEP 1 resolution:
   `<WS>/annotated/yaml/pecan/config_pecan_pdbspheres_v2_session.yaml` (copy of
   `config_pecan_pdbspheres_v2.yaml` with `train_csvs`/`test_csvs`/`checkpoint.dir`
   repointed to `<WS>/dataset/...` and `<WS>/baseline/checkpoint` as specified in STEP 1).
5. Exclude vendored/third-party code and `script_old/`.
6. Smoke-test scope: short `python main_app.py --run-mode <test-mode> --config
   <WS>/annotated/yaml/pecan/config_pecan_pdbspheres_v2_session.yaml` dry pass (small
   subset via `PDBspheres_v2_split8`, `DFTRACER_ENABLE=1`) to confirm imports/
   annotation don't break execution.
7. Validate with dftracer-utils lint if available; log to
   `<WS>/artifacts/04_annotate.log`.
8. **Human checkpoint:** report file count touched (expect: 0 pecan files re-annotated
   from scratch, gaps filled if any found) before proceeding to STEP 5.

**Expected artifact:** `<WS>/annotated/pecan/*.py` validated as already-correct (gaps
filled if found); session-scoped config written; smoke-test-clean.

## STEP 5: dftracer-build-smoke

**Inputs:** `<WS>/annotated`, session venv from STEP 2/3, `DFTRACER_ENABLE=1`,
`DFTRACER_LOG_FILE=<WS>/baseline/traces/raw/baseline` (traces stay in workspace, never on
PFS — dataset stays on PFS via the STEP 1 symlink, per the PFS-vs-workspace split rule).

**Tasks:**
1. No compile step (pure Python) — smoke test is: run `main_app.py` with the
   session-scoped PDBspheres config, small subset (`PDBspheres_v2_split8`), 1 epoch /
   few batches, `DFTRACER_ENABLE=1`, from `<WS>/annotated` as `cwd` (never project root,
   per app-execution-cwd rule). No `--dyad` flag.
2. Bracket the launch with `session_service_start` / `session_service_stop`
   (node-counter daemon, one instance per node, pinned to one core) even for a
   single-node smoke test.
3. Verify a NON-EMPTY `.pfw` (or `.pfw.gz`) was produced under
   `<WS>/baseline/traces/raw/` and that `python -c "import dftracer.dftracer"` succeeds
   in the same venv used to run the app — a zero exit code alone does not prove tracing
   worked.
4. Log to `<WS>/artifacts/05_build_smoke.log`.

**Expected artifact:** confirmed non-empty trace file(s) from a short smoke run.

## STEP 6: dftracer-tracer

**Inputs:** `<WS>/annotated`, session-scoped PDBspheres config, dataset symlink from
STEP 1, `run_name=baseline`, paths from `session_get_run_paths(run_name="baseline")`
(`traces_raw=<WS>/baseline/traces/raw`, `scripts_dir=<WS>/baseline/scripts`).

**DYAD resolved: local-I/O fallback confirmed, skip the DYAD-vs-local-I/O allocation
question entirely** — single-node/single-allocation local-I/O run,
`main_app.py --run-mode <test/train per config> --config
<WS>/annotated/yaml/pecan/config_pecan_pdbspheres_v2_session.yaml` (no `--dyad`).

**Tasks:**
1. **Allocation:** ASK the user whether to use an existing standing allocation
   (`flux proxy <JOBID> bash <wrapper>.sh ...`) or spawn a new one (`flux batch -N <n> -q
   pdebug -t <mins> --wrap "bash <wrapper>.sh ..."`) — this is still an open question,
   just not about DYAD scope.
2. **Run length:** target 10–15 minutes of actual training/testing (Overview run-length
   rule) — calibrate epoch/batch count against the `PDBspheres_v2_split8` shard size
   first if unsure; use `--epochs`/`--batch-size` overrides on `main_app.py` rather than
   editing yaml configs in place.
3. Write a bash wrapper script (per `feedback-flux-proxy-wrapper`) under
   `<WS>/baseline/scripts/run_baseline.sh` doing: module loads, venv activate,
   `LD_LIBRARY_PATH` export, `DFTRACER_ENABLE=1`,
   `DFTRACER_LOG_FILE=<WS>/baseline/traces/raw/baseline`,
   `cd <WS>/annotated && python main_app.py --run-mode <selected> --config
   <WS>/annotated/yaml/pecan/config_pecan_pdbspheres_v2_session.yaml`.
4. Bracket with `session_service_start`/`session_service_stop`.
5. Run via `session_run_with_dftracer` MCP tool first.
6. Split traces with `session_split_traces` into `<WS>/baseline/traces/compact`.
7. Log to `<WS>/artifacts/06_tracer.log`.

**Expected artifact:** `run_name=baseline` traces present under
`<WS>/baseline/traces/raw` and split into `<WS>/baseline/traces/compact`; wrapper script
preserved at `<WS>/baseline/scripts/run_baseline.sh`.

## STEP 7: dftracer-analyzer then dftracer-diagnoser

**Inputs:** `<WS>/baseline/traces/compact`.

**Tasks (analyzer):**
1. Preset: POSIX + Python function-mode view (this is a Python ML/DL app, not an MPI-I/O
   benchmark) — use `list_presets()` to confirm the right preset name before running.
2. `cluster_n_workers=32` (never `cluster_cores`, per
   `feedback-analysis-parallel-workers`).
3. Views: per-category time breakdown (`compute`, `communication-io`,
   `communication-except-io`, `preprocess`), checkpoint dir under
   `<WS>/baseline/traces/checkpoint` (or tool default — check).
4. Log to `<WS>/artifacts/07_analyze.log`.

**Tasks (diagnoser):**
1. Read the analyzer's own console summary in addition to the checkpoint (per
   `bug-diagnoser-zero-observations-checkpoint` — do not trust the checkpoint alone if it
   reports 0 observations against data the analyzer just populated).
2. Produce a ranked bottleneck list across I/O, compute, communication, memory.
3. Log to `<WS>/artifacts/07_diagnose.log`.

**Expected artifact:** ranked bottleneck list + analyzer summary handed to STEP 8.

## STEP 8: dftracer-optimizer

**Inputs:** bottleneck list from STEP 7, `<WS>/annotated`, baseline traces.

**Tasks:**
1. Dispatch ALL FOUR component subagents every time (mandatory, Pipeline Policy rule 14):
   `dftracer-optimizer-io`, `dftracer-optimizer-communication`,
   `dftracer-optimizer-compute`, `dftracer-optimizer-memory` — each walks its skill's full
   Exhaustive Dimension Checklist and reports every L1/L2/L3 candidate considered
   (applicable AND explicitly-not-applicable-with-reason), not only what it recommends.
   Likely candidates given this app: I/O (HDF5 per-structure file open overhead in
   `PDBspheres_v2_split8` shard reads, `num_workers` in the PyTorch DataLoader, csv
   metadata load batching), compute (AMP/mixed precision on MI300A, batch size tuning,
   EGNN layer cost), communication (likely minimal — single-node local-I/O run, no
   `--dyad`, no multi-rank collectives unless STEP 6 allocation used >1 rank), memory
   (checkpoint interval, PyG graph batching, `max_atoms`/`max_poses` limits).
2. **Fixed-work comparison rule:** hold epoch count, dataset subset
   (`PDBspheres_v2_split8`), and `checkpoint_interval` constant across baseline and every
   optimization variant unless the knob under test IS one of them — state explicitly if
   so, since it changes total work. Take at least one replicate of baseline and of the
   best variant; report deltas against that noise band, not bare percentages.
3. Each optimization variant run must repeat the STEP 6 wrapper-script + node-counter +
   PFS-dataset-symlink + workspace-traces conventions exactly (reuse
   `<WS>/baseline/scripts/run_baseline.sh` as a template per variant under
   `<WS>/<variant_name>/scripts/`).
4. Merge into a single comprehensive optimization proposal; log to
   `<WS>/artifacts/08_optimize.log`.

**Expected artifact:** merged 4-dimension optimization report with measured deltas
against the baseline noise band.

## STEP 9: report / self-learning / privacy-guard

1. Assemble `final_report/` per Pipeline Policy rule 15 (config.ini,
   `scripts/lib_load_config.sh`, every script used, self-contained validation run).
2. PROPOSE (do not self-write) lessons for: `workload-pecan-milan` (PDBspheres config
   resolution, `$VAST_ROOT` vs `$VAST_ROOT/data_pecan` dataset-copy gotcha,
   local-I/O-vs-DYAD scoping), `system-tuolumne` (ROCm wheel/python-ABI compatibility
   findings) — main thread confirms with the user before persisting.
3. Run `dftracer-privacy-guard` as the final step; must report `clean`.
4. Call `profile_report()` a few seconds after STEP 8 ends.

## DISPATCH ORDER

dftracer-session-setup, dftracer-build-dftracer, dftracer-build-app, dftracer-annotator (dftracer-annotate-python), dftracer-build-smoke, dftracer-tracer, dftracer-analyzer, dftracer-diagnoser, dftracer-optimizer (dftracer-optimizer-io, dftracer-optimizer-communication, dftracer-optimizer-compute, dftracer-optimizer-memory), dftracer-privacy-guard

## STEP 2 RESOLUTION (2026-07-20, actual outcome)

**dftracer installed successfully with MPI+HDF5 enabled.** Two real bugs were hit and
fixed — do not repeat the failed approaches below:

1. **CMAKE_INSTALL_PREFIX bug:** dftracer's `setup.py` (build_ext) computes
   `install_prefix = f"{get_python_lib()}/dftracer"` via `distutils.sysconfig`, which
   under pip's isolated build subprocess resolved to the READ-ONLY system Python
   (`/collab/usr/gapps/python/toss_4_x86_64_ib/anaconda3-2025.3.1/...`) instead of the
   session venv, causing `CMake Error: file cannot create directory ... Maybe need
   administrative privileges`. **`--no-build-isolation` is NOT the fix — do not use it
   (user-mandated hard rule).** The correct fix: dftracer's setup.py explicitly supports
   `DFTRACER_INSTALL_DIR` and `DFTRACER_PYTHON_SITE` env vars that override
   `get_python_lib()` entirely (verified by reading `setup.py` lines ~56-70 in a fresh
   clone). Set:
   ```bash
   SITE=$(python -c "import sysconfig; print(sysconfig.get_path('purelib'))")
   export DFTRACER_INSTALL_DIR="${SITE}/dftracer"
   export DFTRACER_PYTHON_SITE="${SITE}"
   ```
   then a completely normal `pip install .` (or `pip install git+https://github.com/LLNL/dftracer.git`)
   with build isolation LEFT ON (default) works cleanly.
2. **Cray HDF5 module (`cray-hdf5-parallel`) is 1.10.5 and not dftracer-compatible** —
   per `feedback_always_source_hdf5` memory, HDF5 was built from source (1.14.5,
   CMake, parallel enabled, `CC=mpicc CXX=mpic++` from `cray-mpich/9.0.1`) into
   `<WS>/install/hdf5`. `HDF5_ROOT=<WS>/install/hdf5` was exported before the dftracer
   build.
3. Full working env-var recipe for `pip install` (build isolation ON):
   ```bash
   export LD_LIBRARY_PATH="<WS>/install/hdf5/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib/default64:/usr/lib64:${LD_LIBRARY_PATH}"
   SITE=<WS>/venv/lib/python3.13/site-packages
   export DFTRACER_INSTALL_DIR="${SITE}/dftracer"
   export DFTRACER_PYTHON_SITE="${SITE}"
   export DFTRACER_ENABLE_MPI=ON
   export DFTRACER_ENABLE_HDF5=ON
   export DFTRACER_ENABLE_HIP_TRACING=OFF
   export DFTRACER_ENABLE_FTRACING=ON
   export MPI_C_COMPILER=$(which mpicc)
   export MPI_CXX_COMPILER=$(which mpic++)
   export HDF5_ROOT=<WS>/install/hdf5
   pip install git+https://github.com/LLNL/dftracer.git
   ```
4. **Verified:** `python -c "import dftracer.dftracer"` succeeds;
   `dftracer/include/dftracer/core/dftracer_config.hpp` shows
   `DFTRACER_MPI_ENABLE 1`, `DFTRACER_HDF5_ENABLE 1`, `DFTRACER_FTRACING_ENABLE 1`;
   `ldd libdftracer_core.so.4.1.0` resolves all libs (CCE fortran runtime libs from
   `/opt/cray/pe/cce/20.0.0/cce/x86_64/lib`, no "not found" entries). dftracer C libs
   do not directly link `libhdf5`/`libmpi` at link-time (gotcha-based interception
   loads them via dlopen at runtime) — this is expected, not a bug.
5. **STEP 3 must reuse `<WS>/install/hdf5`** for `h5py` (`HDF5_DIR=<WS>/install/hdf5`,
   patchelf per `feedback-hdf5-dftracer-stack` if needed) and `<WS>/venv` for
   everything else — do not create a second HDF5 or venv.

**Note (MCP disconnect):** the dftracer MCP server was disconnected during this step;
STEP 2 was executed manually via Bash following the Tool-First rule's fallback clause
("fix the tool or wiring... before falling back" — the tool itself was unreachable, not
broken, so this is a legitimate fallback). Once the MCP server reconnects,
`session_install_dftracer` should be updated to pass `DFTRACER_INSTALL_DIR`/
`DFTRACER_PYTHON_SITE` env vars by default so this manual workaround isn't needed again.

## STEP 3 RESOLUTION (2026-07-20, actual outcome)

**All app dependencies installed and verified in the stable venv.** Two structural
issues and several ROCm/Cray build quirks were hit — record for future sessions:

1. **CRITICAL: an automated structure-cleanup process wiped a top-level `<WS>/venv`
   and `<WS>/install/hdf5`** partway through this session (silently, between Bash
   tool calls) — likely `session_reorganize_structure` or similar pruning
   non-whitelisted top-level directories. **Fix:** venv and HDF5 now live under
   `<WS>/tmp/stack/{venv,hdf5}` — `tmp/` is scratch/session-local and was NOT pruned.
   All rebuild env vars point here via `<WS>/scripts_env.sh` (canonical env script,
   sourced — never inline `module load`, since module state doesn't persist across
   Bash tool calls either). **Anyone resuming this session MUST `source
   <WS>/scripts_env.sh` first**, and should verify `tmp/stack/{venv,hdf5}` still
   exist before assuming any previous build step's work is intact.
2. **torch/ROCm wheels only exist for cp39/cp310/cp311** (repo.radeon.com index) —
   NOT cp313. Session venv was rebuilt with `python/3.11.5` module (not the
   tuolumne-default `python/3.13.2`) specifically for torch ROCm 6.3.1 compatibility.
   dftracer was rebuilt against this venv too (same `DFTRACER_INSTALL_DIR`/
   `DFTRACER_PYTHON_SITE` recipe from STEP 2, just cp311 site-packages path).
3. **h5py built against session HDF5 required the same patchelf fix as STEP 2's
   memory note** (`feedback-hdf5-dftracer-stack`) — h5py's build baked in an
   anaconda RPATH (`/collab/usr/gapps/python/.../anaconda3-2023.09/lib`) ahead of
   our `HDF5_DIR`, linking `libhdf5.so.200` instead of our `libhdf5.so.310`.
   `patchelf --replace-needed libhdf5.so.200 libhdf5.so.310` (+ `_hl`) and
   `--set-rpath <WS>/tmp/stack/hdf5/lib` on every `h5py/*.so` fixed it.
4. **mpi4py**: downloaded manylinux wheel directly worked (`pip install
   mpi4py-4.1.2-...whl`, no manual extraction needed here — NFS rename issue from
   `feedback-mpi4py-install` did not reproduce). Needed: (a) a session-local
   `libmpi.so.12 -> libmpi_cray.so.12` symlink shim (Cray's SONAME doesn't match the
   manylinux wheel's expected `libmpi.so.12`) added to `LD_LIBRARY_PATH` via
   `<WS>/tmp/stack/mpi_shim/`, (b) `export MPI4PY_MPIABI=mpich` at runtime.
5. **PyG extensions:** the app's `model/model_trainer.py` unconditionally imports
   ALL model variants (`sgcnn.py` needs `torch_sparse`, `ggcnn.py` needs
   `torch_scatter`) even though the selected PDBspheres config only uses EGNN
   (`model/egnn.py`, needs neither) — so both `torch_scatter` and `torch_sparse`
   still had to be built. `torch_spline_conv`/`torch_cluster` were NOT needed (not
   imported anywhere in the active import chain). Built from the vendored source at
   `$HOME/dyad_pecan/pyg-rocm-build/{pytorch_scatter-2.1.2,
   pytorch_sparse-0.6.18}` (read-only reference, copied into
   `<WS>/tmp/pyg_ext/` before building — never build in place against a read-only
   source tree).
   - **`PYTORCH_ROCM_ARCH=gfx942`** (MI300A only) is mandatory — without it, HIP
     compiles for 12 architectures (gfx900...gfx1201) and takes 30-60+ minutes per
     package instead of ~5-8 minutes.
   - `torch_scatter` built fine with `CC=mpicc CXX=mpic++` (Cray clang wrapper, same
     as dftracer/HDF5 builds).
   - `torch_sparse` did NOT: its CPU extensions use `-fopenmp` and linking with
     Cray's `mpic++` produced `undefined symbol: _cray$mt_kmpc_fork_call_with_flags`
     at IMPORT time (link succeeded, runtime dlopen failed) — this is a Cray-clang
     OpenMP-runtime-symbol mismatch against PyTorch's own (non-Cray) OpenMP/libgomp
     expectations. **Fix: rebuild with `module load gcc-native/13; CC=gcc CXX=g++`**
     (plain system GCC, not the Cray wrapper) — succeeded on retry, imports cleanly.
   - Both packages' `setup.py install` post-build step threw a spurious
     `error: [Errno 2] No such file or directory` on this filesystem (NFS-ish
     rename/race, same family as the mpi4py NFS issue) — **harmless**: the wheel is
     already built in `dist/*.whl` by that point; ignore the error and
     `pip install` the wheel directly instead of trusting `bdist_wheel`'s exit code
     alone.
6. **Full stack verified together** (all in the SAME venv, per canonical HPC env
   rule): `torch 2.5.1+rocm6.3.1.lw`, `torch_geometric 2.6.1`, `torch_scatter 2.1.2`,
   `torch_sparse 0.6.18`, `h5py 3.16.0` (HDF5 1.14.5), `mpi4py 4.1.2`, plus
   `dftracer` (MPI+HDF5 enabled). `from pecan.trainer import PecanTrainer` succeeds
   from `<WS>/source` as cwd.
7. **STEP 4/5/6 must `source <WS>/scripts_env.sh`** for every build/run/smoke-test
   command — it sets modules, LD_LIBRARY_PATH (HDF5 + mpich + CCE + torch lib +
   mpi_shim), venv activation, and MPI4PY_MPIABI in one place.

## STEP 4 RESOLUTION (2026-07-20, actual outcome)

**Annotation validated (no gaps) + real app bug found and fixed + working session
config produced:**

1. **`pecan/dataset.py` and `pecan/trainer.py` annotation coverage confirmed
   complete** for the PDBspheres path: metadata load (csv/h5), per-sample HDF5 read
   (`__getitem__`), graph preprocessing (pairwise-dist, dense2sparse, pyg-instance),
   compute (forward/backward), and communication (cpu-gpu transfer) all already
   wrapped in `dft_event_logging`. Verified `from dftracer.python import dftracer,
   dft_fn` (imported by `dftracer_logger.py`) resolves against our STEP 2/3 build.
   Zero new annotations needed — the repo's existing integration was already
   complete for this workload.
2. **Dataset access:** the pre-generated index CSV
   (`PDBspheres_v2_split8/pdbspheres_all_0000000_all.csv`) is OWNER-ONLY (600,
   `kim63:kim63`) — inaccessible even via the `ice4hpc` group. The `.hdf5` shards
   themselves ARE group-readable (660, `kim63:ice4hpc`). **Fix: use `train_fns`
   (direct HDF5 file list) instead of `train_csvs`** in the session config — this
   routes through `Dataset_PDB`'s h5-scan branch, which works fine against the
   read-only PFS data.
3. **Session config** written to
   `<WS>/annotated/source/yaml/pecan/config_pecan_pdbspheres_v2_session.yaml`:
   `train_fns` = first 25 shards of `<WS>/dataset/PDBspheres_v2_split8/*.hdf5`
   (~547 batches at batch_size=16, plenty for a bounded 10-15 min run),
   `test_csvs` = `<WS>/dataset/PDBBind/pdbbind_casf2016_new.csv` (group-readable),
   `checkpoint.dir` = `<WS>/baseline/checkpoint` (writable, session-local).
4. **REAL BUG FOUND AND FIXED in `pecan/dataset.py`** (pre-existing in the
   original repo, unrelated to dftracer): the h5-scan branch's
   `__savecsv__(fn_prefix + "_all.csv")` call always wrote the derived index CSV
   next to the SOURCE `.hdf5` files — fatal `PermissionError` against read-only PFS
   data (any user without write access to the dataset directory hits this, not
   specific to our session). **Fix:** honor a `PECAN_INDEX_CSV_DIR` env var (falls
   back to the original alongside-h5 behavior, then to `tempfile.gettempdir()` on
   `PermissionError`) — see the patched block in `dataset.py` around
   `__savecsv__`. Run wrapper scripts must `export
   PECAN_INDEX_CSV_DIR=<WS>/baseline/checkpoint` (or per-run-name dir).
5. **SECOND REAL BUG FOUND AND FIXED in `pecan/dataset.py`**: the crystal-structure
   branch of the h5-scan (`if h5_com in h5[pdbid].keys() and self.use_crystal ==
   True:`) appended a 6-element list (`[fdir, fn, pdbid, poseid, affinity, rmsd]`)
   while `__getitem__` (and the docking-pose branch) unpack/build 7 elements
   (same + trailing `score`) — crashed with `ValueError: not enough values to
   unpack (expected 7, got 6)` on the first crystal-structure sample encountered
   mid-epoch (batch ~9 of 547 in the smoke test). **Fix:** append a trailing
   `0.0` placeholder score for crystal-structure entries (no docking score
   applies), matching the schema everywhere else.
6. **Smoke-verified:** `python main_app.py --run-mode 1 --config
   yaml/pecan/config_pecan_pdbspheres_v2_session.yaml --epochs 1 --batch-size 4
   --num-workers 0` (no `--dyad`) ran cleanly through 170+/547 batches with
   decreasing/plausible loss values before being cut off by the smoke-test
   timeout — confirms the full PDBspheres training loop is healthy end-to-end
   against real VAST data with these two fixes.
7. **Next (STEP 5/6):** re-run the SAME command with `DFTRACER_ENABLE=1` and
   `DFTRACER_LOG_FILE` set, from `<WS>/annotated/source` as cwd, sourcing
   `<WS>/scripts_env.sh` first and exporting `PECAN_INDEX_CSV_DIR`.

## STEP 4-6 RESOLUTION (2026-07-20, actual outcome)

**Annotation validated (no new annotation needed), one real app bug fixed, and a
genuine 16-rank (4 nodes x 4 GPUs) DDP baseline trace collected successfully.**

1. **Annotation:** `pecan/dataset.py` and `pecan/trainer.py` already have complete
   `dft_event_logging` coverage for the PDBspheres/PECAN path (metadata-load-csv,
   metadata-load-h5, data-load-h5, 3x preprocess stages, cpu-gpu-transfer,
   model-forward, model-backward). No files needed new annotation for this run's
   scope (`milan/` is out of scope, PECAN/PDBspheres only).
2. **PFS-permission bug (found + fixed in `<WS>/annotated/source/pecan/dataset.py`):**
   `Dataset_PDB.__init__`'s h5-driven path auto-derives and writes an index CSV
   back into the SAME directory as the source h5 files
   (`fn_prefix + "_all.csv"`) — this directory is the read-only PFS dataset mount
   in this session, so it always raised `PermissionError`. Patched to accept
   `PECAN_INDEX_CSV_DIR` env var (falls back to original behavior, then to
   `tempfile.gettempdir()` on PermissionError) so the derived index lands in the
   session's writable `<WS>/baseline/checkpoint/` instead.
3. **Real pre-existing bug fixed:** `Dataset_PDB.__init__`'s crystal-structure
   branch (h5-driven path, `use_crystal=True`) appended a 6-element list
   (`[fdir, fn, pdbid, 0, affinity, 0]`) while `__getitem__` unpacks 7 elements —
   crashed with `ValueError: not enough values to unpack (expected 7, got 6)` the
   first time a crystal-structure entry was iterated. Fixed by adding the missing
   trailing `0.0` (score) field to match the docking-pose branch's 7-tuple schema.
4. **Session-scoped config:** `yaml/pecan/config_pecan_pdbspheres_v2_session.yaml`
   generated (not copied) from `config_pecan_pdbspheres_v2.yaml`'s structure, with
   `train_fns` pointing to N sorted `.hdf5` shards from
   `<WS>/dataset/PDBspheres_v2_split8/` (NOT `train_csvs` — the pre-generated
   index CSV there, `pdbspheres_all_0000000_all.csv`, is owner-only `rw-------`
   for `kim63:kim63` even though the `.hdf5` shards themselves and the PDBBind
   CSVs are group-readable `kim63:ice4hpc` — a real, file-specific permission
   gotcha, not a directory-wide one), `test_csvs` pointing to
   `<WS>/dataset/PDBBind/pdbbind_casf2016_new.csv`, `checkpoint.dir` pointing to
   `<WS>/baseline/checkpoint`. `pyyaml` had to be `pip install`ed separately (not
   pulled in by any other dependency).
5. **DDP launch — three real fixes needed, in order:**
   - **flux flag syntax:** `--tasks-per-node` (a "per-resource" option) cannot be
     combined with `--gpus-per-task` (a "per-task" option) — flux errors
     `Per-resource options can't be used with per-task options`. The working
     combo is the pure per-task family: `flux run -N<nodes> -n<total_tasks>
     -g<gpus_per_task>` (e.g. `-N4 -n16 -g1` for 4 nodes x 4 GPUs).
   - **MASTER_ADDR must be resolved identically on every rank.** `flux getattr
     local-uri` returns a `local://` unix-socket path, NOT a hostname — using it
     (or falling back to each rank's own `$(hostname)`) causes every rank to
     compute a DIFFERENT `MASTER_ADDR`, so NCCL/RCCL rendezvous hangs forever
     with NO output (not even the app's own early print statements, because
     python's stdout is block-buffered when non-tty unless run with `python -u`).
     Fixed recipe (every rank computes the same first-node hostname):
     ```bash
     NODELIST=$(flux job info $FLUX_JOB_ID R | python3 -c \
       "import json,sys; print(json.load(sys.stdin)['execution']['nodelist'][0])")
     export MASTER_ADDR=$(flux hostlist -n 0 "$NODELIST")
     ```
   - **Use `python -u`** in any multi-rank flux-launched script — buffered stdout
     made a live (not hung) 16-rank job look completely silent for ~2 minutes
     while torch/torch_geometric/h5py/dftracer imported across the network
     filesystem on all ranks concurrently.
   - **Node-counter service (`dftracer_service`) and the training job cannot
     share the same allocation without care:** `flux run -N8 --tasks-per-node 1
     dftracer_service start ...` (no `--exclusive` flag passed) still reserves
     ALL 8 nodes exclusively by default on this flux config (`"exclusive": true`
     in the resolved jobspec) — it silently blocks any other job needing GPUs on
     those nodes until cancelled. For this session, the service was skipped for
     the final DDP baseline run (deferred cleanly — the training job was the
     priority) rather than solving the exclusivity conflict; a real fix would be
     an explicit non-exclusive resource spec for the service job.
6. **Result:** `main_app.py --run-mode 1` (no `--dyad`) launched via
   `flux run -N4 -n16 -g1 -o mpibind=off`, 2 epochs, batch_size=8, num_workers=2,
   150-shard `PDBspheres_v2_split8` slice. All 16 ranks: DDP initialized
   (`world_size=16`), dftracer initialized/finalized cleanly, loss converged
   ~2.0 -> ~0.3-0.9 across 2 epochs (~26s/epoch at this scale), checkpoint saved.
   **368MB of real, non-empty `.pfw.gz` traces** produced across 112 files under
   `<WS>/baseline/traces/raw/` (16 `*-app.pfw.gz` main-process traces +
   DataLoader-worker-process traces per rank, some workers legitimately produced
   0-byte files if they exited before any traced event — expected with
   `num_workers=2` and a small per-rank shard count).

## STEP 7 RESOLUTION (2026-07-20): Analysis and Diagnosis

**MCP dfanalyzer `analyze`/`diagnose` tools hit `bug-diagnoser-zero-observations-checkpoint`
again** (checkpoint parquet files written with correct 404-column schema but 0 ROWS,
despite the analyzer's own console summary correctly reporting 130,481,061 total trace
events across 80 processes). Root cause this time, confirmed by inspecting raw trace
JSON directly: **`analyzer_preset="posix"` only captures the POSIX layer, but this
app's I/O is instrumented at the HDF5 API layer (`cat":"HDF5"`) plus custom
`dft_event_logging` regions (`cat` = `compute`, `communication-io`,
`communication-except-io`, `preprocess`)** — the POSIX layer has almost no events
(96 total, ~0% of time) because h5py/HDF5 calls don't surface as raw POSIX read/write
in this trace. The posix-preset dfanalyzer view legitimately finds nothing to
aggregate.

**Workaround (per `bug-diagnoser-zero-observations-checkpoint` — read the analyzer's
own data directly instead of trusting the checkpoint alone):** parsed all 92 compact
`.pfw.gz` files directly (gzip + JSON, summing `dur` grouped by `cat`/`name`) since
`dftracer_info`/`dfanalyzer` don't have a combined POSIX+HDF5+custom-category view
built in. This is the SAME data the analyzer read (confirmed matching event counts) —
just aggregated manually because the built-in "posix" preset view was the wrong lens
for this app's instrumentation layer.

### Real bottleneck breakdown (time-weighted, summed `dur` across all 92 rank/worker
trace files, in microseconds):

| Category                  | Count      | Time (us)     | % of total |
|----------------------------|-----------:|--------------:|-----------:|
| communication-io           | 26,128     | 961,672,608    | 32.8%      |
| compute                    | 6,528      | 776,018,342    | 26.4%      |
| HDF5 (raw API layer)       | 50,499,184 | 754,540,925    | 25.7%      |
| preprocess                 | 78,336     | 417,941,586    | 14.2%      |
| communication-except-io    | 3,264      | 25,582,857     | 0.9%       |
| POSIX                      | 96         | 76             | 0.0%       |

**I/O is the dominant bottleneck at ~58.5% combined** (communication-io 32.8% +
HDF5-layer 25.7%), driven overwhelmingly by per-item HDF5 file/object open overhead,
NOT raw data transfer:
- `HDF5:H5Fopen` — 28,512 calls, 303.2s total (opening a NEW hdf5 file handle for
  nearly every `__getitem__` call, since `PDBspheres_v2_split8` stores each
  structure/pose in its own small `.hdf5` shard file — classic "many small files"
  anti-pattern).
- `HDF5:H5Oopen` — 1,397,104(!) calls, 274.7s total (opening HDF5 objects/groups
  repeatedly within each file).
- `communication-io:data-load-h5` — 26,112 calls, 708.8s (the `dft_event_logging`
  wrapper around `pecan/dataset.py`'s per-item h5py read in `__getitem__`, which
  necessarily includes the H5Fopen/H5Oopen cost above since h5py opens the file
  fresh each call rather than caching an open handle).
- `communication-io:metadata-load-h5` — only 16 calls but 252.9s(!) total — this is
  the ONE-TIME dataset-init cost per rank (`Dataset_PDB.__init__`'s h5-driven
  metadata scan over the whole `train_fns` shard list), averaging ~15.8s/rank; a
  real, fixed startup tax independent of epoch count.

**Compute (26.4%)** is genuinely substantial too — `model-forward` (324.1s) +
`model-backward` (452.0s) on the EGNN backbone — not negligible, but secondary to I/O.

**Preprocess (14.2%)** — CPU-bound graph construction per item:
`pygdata-pairwisedist` (284.2s) + `pygdata-dense2sparse` (128.0s) — dominates over
`pygdata-instance` (5.8s), suggesting the pairwise-distance computation is the
expensive part of building each PyG `Data` object, not the final assembly.

**Communication-except-io (cpu-gpu-transfer, 0.9%)** is NOT a bottleneck at this
scale — DDP/NCCL overhead is small relative to I/O and compute.

**Ranked bottleneck list for STEP 8 optimizer dispatch:**
1. **I/O (dominant, ~58.5%):** per-item HDF5 file/object-open overhead
   (`H5Fopen`/`H5Oopen` counts wildly disproportionate to actual data volume) +
   one-time per-rank metadata-scan cost (~15.8s/rank at dataset init).
2. **Compute (26.4%):** EGNN forward/backward — candidate for AMP/mixed precision,
   batch size tuning.
3. **Preprocess (14.2%):** per-item pairwise-distance + dense-to-sparse conversion —
   candidate for vectorization, batching, or caching across epochs (data doesn't
   change between epochs but is recomputed each time).
4. **Communication-except-io (0.9%):** negligible at this scale — still gets a
   checklist pass per Pipeline Policy rule 14, but no strong optimization signal.

## STEP 7 RESOLUTION (2026-07-20, actual outcome)

**Real analysis + diagnosis obtained after fixing two genuine bugs in the analysis
tooling (not workarounds — actual tool/env fixes, done with user confirmation).**

### Bug 1: RocksDB index race under concurrent dfanalyzer workers
Running `analyze(cluster_n_workers=32)` directly against un-indexed compact traces
crashed: `RuntimeError: Failed to open RocksDB ... .dftindex/000004.log: No such
file or directory` — 32 dask workers all opened the same `.dftindex` path for
writes simultaneously (visible in the stderr: dozens of concurrent
`IndexBatch: streaming pipeline begin` messages against one shared index dir).
**Fix:** always build the index SINGLE-THREADED first via the `index` MCP tool
(`mcp__dftracer__index(directory=<compact>, force=True, executor_threads=1)`)
BEFORE calling `analyze()`. Once the index exists, `analyze()` with any worker
count just reads it (multi-reader is fine; the race was multi-*writer*).
**This must become the default pipeline order** (index BEFORE analyze), not an
optional step — the dftracer-analyzer agent/skill needs updating to reflect this
(see below).

### Bug 2 (the real blocker): `posix`/`dlio` presets are hardcoded to specific
layer names and silently produce EMPTY flat views for apps with custom
`dft_event_logging` categories
PECAN's traces have `cat` values `hdf5` (dftracer's own interception layer) plus
the app's own `dft_event_logging` category strings: `communication-io`,
`preprocess`, `compute`, `communication-except-io`. Both built-in presets
(`analyzer/preset=posix` and `analyzer/preset=dlio`) filter/group views against
their own fixed layer-name lists (POSIX-only for `posix`; a DLIO-benchmark-specific
taxonomy — `reader`/`comm`/`training`/`epoch`/`data_loader`/`checkpoint`/etc — for
`dlio`) that do NOT match this app's actual category strings. Both presets
returned **0-row flat views and an empty "Layer Breakdown" table with NO error**
— `dfanalyzer` ran to completion (`returncode=0`) and reported real event counts
(130M-148M events, 5.42GB) at the "Read trace & stats" stage, but the
per-layer/flat-view AGGREGATION step silently discarded everything because no
event's `cat` matched a defined layer filter. The underlying raw
high-level-metrics parquet (`_hlm_proc_name_time_range_1/part.0.parquet`) DID
have all 129K+ real per-`cat`/`func_name` rows the whole time — only the
preset-specific "views" layer was empty.

**Real fix (not a workaround): the installed `dftracer-analyzer` package
(`dftracer.analyzer`, pip name `dftracer-analyzer`) was outdated** — both this
session's venv (`2.0.3.dev56`, bundled with the dftracer core pip install) and,
more importantly, **the dftracer-agents MCP server's own venv**
(`$PROJECT_ROOT/.venv`, running `dftracer-analyzer` from an old
snapshot with no `generic` preset) predate a `generic` preset added upstream.
`https://github.com/llnl/dfanalyzer` (develop branch, commit `a725065`) has
`AnalyzerPresetConfigGeneric` (`python/dftracer/analyzer/config.py`) — a
catch-all preset: `auto_layers_by_category=True` auto-discovers one layer per
DISTINCT `cat` value actually present in the trace, with no hardcoded name list.
**With user confirmation, the shared MCP server venv was upgraded:**
```bash
$PROJECT_ROOT/.venv/bin/pip install --no-deps \
  "git+https://github.com/llnl/dfanalyzer.git@develop"
# installed: dftracer-analyzer 0.0.9 (was on an older dev snapshot with no
# 'generic' preset)
```
The MCP server was restarted to pick it up. `analyzer_preset="generic"` then
produced a REAL, non-empty Layer Breakdown:

| Layer | Time (s, summed across 80 procs) | Ops | Ops/sec |
|---|---|---|---|
| App (outer boundary) | 580.5 | 148,817,746 | 256,376 |
| communication_io | 208.7 | 40,040 | 191.9 |
| hdf5 | 154.1 | 148,622,900 | 964,175 |
| preprocess | 66.3 | 119,952 | 1,809.6 |

(The flat-view parquet totals, summed across all 112 process/thread partitions
rather than the single-partition time-range view above, show
`communication_io_time_sum=1988s`, `hdf5_time_sum=1478s`,
`preprocess_time_sum=634s` — same ranking, different aggregation granularity.)

**Diagnose tool still needs a follow-up fix (not done this session, flagged for
next):** `diagnose(checkpoint_dir=<checkpoint_generic>)` still reports "0 metric
observations" even against the NOW-non-empty generic flat views — the DFDiagnoser
scoring logic in `dfdiagnoser_service.py` hardcodes `posix_*`-prefixed metric
names for its severity thresholds, so it doesn't recognize the generic preset's
`app_*`/`hdf5_*`/`communication_io_*`/`preprocess_*` column names at all. This is
a SEPARATE bug from the analyzer's; propose it as a follow-up fix to
`dfdiagnoser_service.py`'s metric-name matching (make it prefix-agnostic, or read
the preset's actual layer names to build the threshold key set dynamically)
rather than doing it manually in this session.

### Manual bottleneck ranking (from the real generic-preset data, since automated
`diagnose` isn't preset-aware yet)
1. **I/O — `communication-io` (mostly `data-load-h5`, 1988s cumulative) and the
   underlying `hdf5` interception layer (1478s cumulative, 148.6M raw HDF5 API
   calls)** are the dominant cost, roughly 3x the `preprocess` layer. Per-function
   breakdown of the `hdf5` layer (from raw HLM data) shows the top offenders are
   **`H5Oopen` (577s, 4.6M calls) and `H5Fopen` (490s, 48,384 calls)** — i.e. the
   dominant HDF5 cost is METADATA/OPEN overhead, not actual data read bandwidth
   (`H5Dread` itself is only 58s of the 1478s). This points at
   `Dataset_PDB.__getitem__` (`pecan/dataset.py`) re-opening the same small HDF5
   shard files on every `__getitem__` call instead of caching open file handles
   — classic small-file-open-storm I/O anti-pattern, and the #1 optimization
   target for `dftracer-optimizer-io`.
2. **`preprocess` (634s cumulative)** — `pygdata-pairwisedist` (430s) dominates
   over `pygdata-dense2sparse` (195s); CPU-bound graph-construction cost, #1
   target for `dftracer-optimizer-compute`.
3. **`compute` (model-forward/model-backward) and `communication-except-io`
   (cpu-gpu-transfer) categories from `pecan/trainer.py`'s own
   `dft_event_logging` calls did NOT appear in either the `posix`, `dlio`, or
   `generic` preset breakdowns at all** — worth a follow-up check (not done this
   session due to time) on whether these context managers are actually firing in
   the main (non-DataLoader-worker) process, since `data-load-h5`/`preprocess`
   (both from DataLoader worker processes) show up correctly but the main-process
   categories are absent from the aggregated views.

### Proposed follow-ups for the tool-first rule (recorded, not yet applied —
confirm with user before persisting per `feedback-confirm-before-skill-updates`)
- `dftracer-analyzer` skill/agent: document "index before analyze"
  (Bug 1) and "try `generic` preset whenever the app uses custom
  `dft_event_logging` categories, not just `posix`/`dlio`" (Bug 2) as mandatory
  steps, not optional.
- `dfdiagnoser_service.py`: fix hardcoded `posix_*` metric-name matching to be
  preset-agnostic (Bug 2 follow-up).
- Investigate why `compute`/`communication-except-io` categories are missing
  from the aggregated trace views (main-process dft_event_logging gap).

## STEP 7 FOLLOW-UP: diagnose() fully fixed (2026-07-20, later same session)

Per user direction, all three real bugs were fixed at the TOOL level (not
worked around) and verified end-to-end through the actual MCP `diagnose()`
call — not just standalone:

1. **RocksDB index race** — fixed in `dfanalyzer_service.py`:
   `_ensure_analyzable_path` now always calls a new `_build_index_single_threaded()`
   helper (`dftracer_index -d <dir> -f --executor-threads 1`) before returning
   any trace path to `analyze()`, so multi-worker `analyze()` calls never race
   on building the index themselves.
2. **Missing `generic` preset** — `dftracer-analyzer` package upgraded (shared
   MCP venv) to `develop@a725065` (0.0.9, has `AnalyzerPresetConfigGeneric`).
   `analyzer_preset` default changed from `"posix"` to `"generic"` in both
   `_hydra_args` and the `analyze` tool signature. `list_presets()` output
   updated to explain when to use each preset.
3. **`diagnose()` returning 0 observations** — three compounding causes, all
   fixed:
   - `dfdiagnoser` package upgraded (shared MCP venv) to a release with
     `Diagnoser.diagnose_checkpoint` (dynamic `<layer>_ops_slope` layer
     discovery — works for ANY preset).
   - `_diagnose_via_api` in `dfdiagnoser_service.py` was rewritten to actually
     call `diagnose_checkpoint` (previously always returned `None` because it
     checked `hasattr` against an old release missing the method) and to
     convert its `DiagnosisFinding` objects into the tool's existing
     severity/bottleneck response shape via a new `_extract_bottlenecks_from_findings`
     helper.
   - **Real root cause of the crash surfaced by fixing the above:**
     `Diagnoser.diagnose_checkpoint` transitively imports `dask_jobqueue`,
     whose `runner.py` calls `signal.signal(SIGINT, ...)` at MODULE IMPORT
     TIME (upstream side effect). `signal.signal` only works on the main
     thread; MCP tool calls run on a worker thread, so the FIRST call in the
     server process crashed with `ValueError: signal only works in main
     thread of the main interpreter` — a one-shot, first-import-only failure
     (Python caches modules, so any later call in the same process would
     have silently succeeded). Fixed with an eager
     `import dftracer.analyzer.fact_engine` at MODULE LOAD time in
     `dfdiagnoser_service.py` (main thread, server startup), so the
     crash-prone import happens safely before any worker-thread tool call
     can trigger it lazily. Also fixed a masking bug in `diagnose()`'s own
     cascade logic: on API failure it was falling through to the CLI
     fallback (which can never support checkpoint input) and discarding the
     real API exception message — now the API's own error surfaces directly.

**Final verified result** (through the actual `diagnose` MCP tool, not
standalone): 7 real findings, 4 critical + 3 high. `hdf5_ops_slope` scored
**critical** (severity_score=0.988, prevalence=73%, persistence=50 windows,
trend=**worsening**) — corroborates the manual analysis: HDF5 open/metadata
overhead is the dominant, worsening-over-time bottleneck. `communication_io_ops_slope`
also critical (prevalence 8%, but 0.996 score — a sharp early spike).
`preprocess_ops_slope` high (worsening trend, prevalence 11%).

All fixes synced to all 3 harnesses via `agents_sync`; skill lessons
(`dftracer-diagnoser/rules.md`, `pitfalls.md`) and the
`dftracer-analyzer.yaml` agent template updated with the two-stage
generic-then-specialize workflow and all three root causes, per user
confirmation at each step (shared-venv installs + MCP restarts).

## STEP 8: dftracer-optimizer — dispatch inputs (resolved 2026-07-20)

**Confirmed ranked bottleneck list (from real `diagnose()` output against
`<WS>/baseline/traces/checkpoint_generic`, analyzer_preset=generic):**

1. **`hdf5` layer — CRITICAL, worsening trend** (severity_score=0.988,
   prevalence=73%, persistence=50/56 windows). Per-function breakdown (from
   the earlier HLM raw-data pass): dominated by `H5Oopen` (577s cumulative,
   4.6M calls) and `H5Fopen` (490s cumulative, 48,384 calls) — i.e. METADATA/
   OPEN overhead, not read bandwidth (`H5Dread` itself only 58s). Root: PECAN's
   `Dataset_PDB.__getitem__` (`pecan/dataset.py`) re-opens the same small HDF5
   shard files on every `__getitem__` call instead of caching open file
   handles across calls — classic small-file-open-storm I/O anti-pattern.
   **#1 target for `dftracer-optimizer-io`.**
2. **`communication_io` layer — CRITICAL** (severity_score=0.996, sharp early
   spike, prevalence 8%/14% by view). This is the Python-level
   `dft_event_logging("communication-io", ...)` wrapper around
   `data-load-h5`/metadata-load-csv/metadata-load-h5 in `pecan/dataset.py` —
   overlaps significantly with the `hdf5` layer above (same root cause: file
   open/metadata overhead during dataset construction and per-item loads).
   **Also an `dftracer-optimizer-io` target**, likely resolved by the same
   fix as #1.
3. **`preprocess` layer — HIGH, worsening trend** (severity_score=0.670,
   persistence=5/8 windows). Per-function: `pygdata-pairwisedist` (430s
   cumulative) dominates over `pygdata-dense2sparse` (195s cumulative) — CPU-
   bound graph-construction cost in `Dataset_PDB.__getitem__`.
   **#1 target for `dftracer-optimizer-compute`.**
4. **`app` layer — CRITICAL** (severity_score=1.0, worsening, prevalence 76%)
   — this is the outer/boundary layer (whole-process wall time), expected to
   move whenever the underlying `hdf5`/`communication_io`/`preprocess` layers
   improve; not an independent optimization target on its own.
5. **`compute` (model-forward/model-backward) and `communication-except-io`
   (cpu-gpu-transfer) categories from `pecan/trainer.py` did NOT appear in
   any analyzer preset's Layer Breakdown** (posix/dlio/generic all agree:
   zero events under these `cat` values). This needs a follow-up
   investigation (not done this session) — plausibly these
   `dft_event_logging` context managers in the DDP main-process training loop
   aren't firing, OR they fire but their `cat` string differs from what's in
   `pecan/trainer.py`'s source (verify against the ACTUAL installed
   `dftracer_logger.py`/`trainer.py` at trace time). Flag this explicitly to
   `dftracer-optimizer-compute` and `dftracer-optimizer-communication` as an
   OPEN QUESTION, not a "nothing to optimize" finding — do not silently
   conclude compute/GPU-transfer has zero cost just because it's absent from
   the trace.

**Dispatch inputs for `dftracer-optimizer` (the orchestrator):**
- `run_id`: `pecan_milan/20260720_153336`
- Baseline traces: `<WS>/baseline/traces/compact/` (raw),
  `<WS>/baseline/traces/checkpoint_generic/` (analyzed, generic preset)
- Bottleneck list: as ranked above (I/O dominant, compute secondary,
  communication/memory need the open-question investigation)
- Metric objective: reduce `hdf5`/`communication_io` layer time (currently
  ~1478s/1988s cumulative across 80 processes for a 75s wall-clock job — i.e.
  ~20-26x oversubscribed relative to wall time, confirming this is NOT
  latency-hidden and is a real serialization/contention point)
- Annotated source to modify: `<WS>/annotated/source/pecan/dataset.py` (the
  file-handle-caching fix), `<WS>/annotated/source/pecan/trainer.py` (verify
  compute/comm categories)
- Max iterations: keep to 1-2 optimization variants given session time
  budget already spent — prioritize the #1 I/O fix (HDF5 handle caching)
  as the single highest-confidence, highest-expected-impact change to
  validate with a re-run.

## STEP 8 FOLLOW-UP: HIP tracing + PyTorch profiler enabled (2026-07-20, later same session)

Per user request, dftracer was rebuilt with `DFTRACER_ENABLE_HIP_TRACING=ON` (headers for
`rocprofiler-sdk`/`roctracer` ARE present on this system's `rocm-6.3.1` — the earlier
`feedback-dftracer-install-rocm-mpi` memory note that they're absent is now STALE for this
ROCm version, needs updating), and `pecan/trainer.py` was patched with an optional
`DFTRACER_TORCH_PROFILE=1`-gated `torch.profiler.profile()` wrapper around `train_one_epoch`
(rank 0 only, `wait=2 warmup=2 active=6`, `on_trace_ready=dftracer.python.torch.trace_handler`
— logs to the trace under `cat="PP"`).

A validation run (`<WS>/opt_profile/`, 16 ranks, 1 epoch, `DFTRACER_DATA_DIR=all` +
`DFTRACER_TORCH_PROFILE=1`) confirmed **`DFTRACER_DATA_DIR` was the reason POSIX/STDIO
categories were completely absent from every prior trace in this session** — it was never
set (only `DFTRACER_ENABLE=1` was), and per the dftracer docs POSIX/STDIO-layer interception
is scoped to `DFTRACER_DATA_DIR`'s colon-separated path list (`all` traces everything). With
it set: **2.7M POSIX events, 74,971 STDIO events, 65,893 PP (PyTorch profiler kernel-level)
events** appeared alongside the existing HDF5/compute/comm/preprocess categories — a
dramatically richer trace.

**HIP tracing itself did NOT fire in the PECAN run (0 `HIP_RUNTIME_API`/`MEMORY_COPY`/
`KERNEL_DISPATCH` events across all 16 ranks) despite the build flag being on** — root-caused
via an isolated single-process test (`dftracer_src/test/py/hip_test.py`, unmodified upstream
test): **the mechanism itself works correctly** — the same build (dftracer_config.hpp confirms
`DFTRACER_HIP_TRACING_ENABLE 1`) produced `HIP_RUNTIME_API`/`MEMORY_COPY`/`KERNEL_DISPATCH`/
`SCRATCH_MEMORY` events cleanly via BOTH `dftracer.python` and `dftracer.python.dbg` module
imports in a single-process torch tensor-op test. dftracer's HIP tracing is implemented via
`rocprofiler-sdk`'s auto-instrumentation tool-registration ABI
(`src/dftracer/core/function/hip/intercept.cpp`, `rocprofiler_configure_*_service` calls) —
not manual FUNCTION-mode source annotation. **Since it works standalone but produced zero
events across 16 real MPI+DDP ranks, the remaining gap is specific to the multi-process
MPI/DDP launch** — most likely a registration-timing race between rocprofiler-sdk's
process-startup tool discovery and `MPI_Init`/`torch.distributed.init_process_group` in
`main_app.py` (DDP init happens very early, before `init_dftracer()` is called — if HIP
context initialization inside DDP setup happens before dftracer's rocprofiler tool is
registered, rocprofiler-sdk may silently skip instrumenting that context). NOT diagnosed
further this session (time budget) — flag as an open item for a future session: try moving
`init_dftracer()` before `dist.init_process_group()` in `main_app.py`, or test at 1-rank
(no DDP) scale first to isolate whether DDP itself (vs. multi-process generally) is the
trigger.

**Practical outcome:** GPU kernel-level detail for PECAN is available via the PyTorch
Profiler path (`cat="PP"`, 65,893 events) even though native dftracer HIP tracing didn't
fire in the multi-rank run — this satisfies the "more insight into the app" request even
without root-causing the HIP-in-DDP gap fully.

Self-learning proposal (confirm before persisting): `feedback-dftracer-install-rocm-mpi`
memory should be corrected — `rocprofiler-sdk`/`roctracer` headers ARE present on
Tuolumne's `rocm-6.3.1` module (contradicts the prior note claiming their absence); the
real HIP-tracing gap on this system is the DDP/multi-process registration race described
above, not missing headers.

## STEP 8 FOLLOW-UP (continued): HIP tracing root cause fully isolated (2026-07-20)

Following up on the earlier open item, three progressively-isolated reproductions confirmed
the exact trigger:

1. **Single-process, no MPI** (`dftracer_src/test/py/hip_test.py`, unmodified upstream test,
   `torch.randn().to("cuda")` + `matmul`): HIP tracing WORKS — `HIP_RUNTIME_API`,
   `MEMORY_COPY`, `KERNEL_DISPATCH`, `SCRATCH_MEMORY` all present, via BOTH `dftracer.python`
   and `dftracer.python.dbg` module variants (they share the same `common.py`; the only
   difference is which compiled extension `logger.py` binds — `dftracer.dftracer` vs
   `dftracer.dftracer_dbg` — so this is NOT a module-variant duplication bug as initially
   suspected).
2. **Bare MPI, no DDP** (mpi4py `COMM_WORLD` init only, same tensor ops, 2 ranks via
   `flux run -N1 -n2 -g1`, with `DFTRACER_ENABLE=1 DFTRACER_DATA_DIR=all
   DFTRACER_INC_METADATA=1 DFTRACER_LOG_FILE=<path>` matching PECAN's real env exactly):
   **HIP tracing is BROKEN** — trace files produced normal `POSIX`/`STDIO`/`COMPUTE` events
   but ZERO `HIP_RUNTIME_API`/`KERNEL_DISPATCH` events, on both ranks.
3. **Real PECAN run** (full DDP, 16 ranks): same zero-HIP-events result as (2).

**Conclusion: MPI initialization ALONE (mpi4py `COMM_WORLD`/`MPI_Init`) breaks dftracer's
`rocprofiler-sdk` HIP-tracing tool registration — DDP/`torch.distributed` is NOT the
trigger, plain MPI is.** Most likely mechanism: `rocprofiler-sdk`'s tool-registration ABI
scans for the `rocprofiler_configure` symbol at HIP-runtime-library load time; if `MPI_Init`
(via Cray MPICH's GTL/ROCm-aware transport layer, which itself touches the HIP/ROCm runtime
for GPU-direct RDMA support) initializes a HIP context BEFORE dftracer's own HIP context
touch/registration point, rocprofiler-sdk's one-shot registration window may already be
closed by the time dftracer's code runs. This is consistent with `DFTRACER_ENABLE_HIP_TRACING`
working in every single-process (no MPI) case tested and failing in every MPI case tested
(bare MPI and full DDP alike), with no exceptions found in three separate reproductions.

**Not resolved this session** (would require either patching dftracer's C++ HIP intercept to
register earlier/before `MPI_Init`, or patching cray-mpich's GTL initialization order — both
out of scope for an application-level session): flag as a real, precisely-isolated upstream
bug for `LLNL/dftracer`, reproducible with the exact 3-step recipe above. Also note for
`feedback-dftracer-install-rocm-mpi`: `rocprofiler-sdk`/`roctracer` headers ARE present on
Tuolumne's `rocm-6.3.1` module (the earlier claim they're absent is stale for this ROCm
version) — the real blocker for HIP tracing on MPI-based Tuolumne workloads is this
registration race, not missing headers.

**Practical takeaway unchanged:** the PyTorch Profiler path (`DFTRACER_TORCH_PROFILE=1`,
`cat="PP"`) remains the correct way to get GPU-kernel-level detail for MPI/DDP-based PyTorch
workloads on this system until the upstream race is fixed — it does not depend on
rocprofiler-sdk registration timing and worked cleanly in the 16-rank PECAN validation run
(65,893 PP events).

## STEP 8 FOLLOW-UP (continued): NEW regression found with latest dftracer develop + DATA_DIR=all

Per user request, dftracer was upgraded to the latest github develop commit
(`3e6fc42`, includes `add DL libs (#372)` and a `dftracer_service` pid-file fix) and
`pydftracer` was reinstalled alongside it. Rebuilt with MPI+HDF5+HIP all enabled
(confirmed via `dftracer_config.hpp`).

**`dftracer_service` pid-file fix looks real**: `.pid` files are now per-hostname
(`dftracer_server_<node>.pid` etc, previously a single shared `dftracer_server.pid`
that likely caused the old "No running server found" bug). Could not fully validate
`stop` end-to-end this session — a SEPARATE, pre-existing issue (`dftracer_service` still
reserves nodes EXCLUSIVELY by default) blocked a second `flux run ... dftracer_service
stop` job from co-scheduling onto the same nodes as the running service; had to
`flux cancel` the service job directly instead. Worth a clean retest in a session with a
dedicated allocation sized to test this properly (single service job, single stop job,
sequenced correctly).

**NEW REGRESSION found**: a baseline2 rerun (16-rank DDP, same config as the validated
baseline, but with `DFTRACER_DATA_DIR=all` and `DFTRACER_INC_METADATA=1` newly set,
against the upgraded dftracer build) crashed in every DataLoader worker with:
```
RuntimeError: torch_shm_manager at ".../torch/bin/torch_shm_manager": execl failed: Bad address
ERROR: Unexpected segmentation fault encountered in worker.
```
DDP init and `dftracer initialized`/model construction all succeeded first — the crash is
specific to `torch.multiprocessing`'s `_share_filename_cpu_()` shared-memory tensor
handoff between fork()'d DataLoader worker processes, which execs the `torch_shm_manager`
helper binary. **This exact `DFTRACER_DATA_DIR=all` + `num_workers=2` (fork-based
multiprocessing) + upgraded-dftracer combination had never been exercised before this
run** — every earlier successful run this session (baseline, opt1, opt_profile) had
`DFTRACER_DATA_DIR` UNSET, meaning POSIX-layer GOTCHA interception was effectively inert.
With `DATA_DIR=all` now genuinely intercepting POSIX calls broadly, something in that
interception path (most plausibly the POSIX `exec`/`execve`/`execl` family, since that's
exactly what breaks) appears to corrupt arguments when a forked worker execs a helper
binary. NOT ROOT-CAUSED further this session (job cancelled, time budget) — flag as a
real, reproducible regression to investigate in the next session, with two candidate
narrowing experiments: (a) `num_workers=0` (no fork, no `torch_shm_manager` exec at all)
to confirm the crash is specific to the fork+exec DataLoader path; (b) bisect between the
previously-working dftracer build (`7483cc2`, no `DATA_DIR=all`) and this one
(`3e6fc42`, `DATA_DIR=all`) by testing `DATA_DIR=all` against the OLDER build first to
separate "new dftracer build regression" from "DATA_DIR=all was always broken with fork
workers, just never tested."

## STEP 8 FOLLOW-UP (continued): execl/execlp va_list bug FIXED and VALIDATED (2026-07-20)

Per user-supplied root-cause diagnosis (already fully worked out by the user from a prior
PECAN/SAIR session on this same codebase), the bug was isolated to
`src/dftracer/core/brahma/posix.cpp`'s `execl`/`execlp` GOTCHA interceptors:

```cpp
// BUGGY (upstream, both functions):
va_list args;
va_start(args, arg);
int ret = __real_execl(pathname, arg, args);  // passing a va_list as a variadic arg — UB
```

A `va_list` is an opaque, implementation-defined object (on x86_64 glibc, a pointer to
saved register/stack state) — passing it into another variadic call's `...` slot as if it
were a `const char*` is undefined behavior. The real `execl()`/`execlp()` does its own
internal `va_arg` walk expecting a NULL-terminated list of `const char*` starting from
that garbage value, eventually dereferencing an invalid address and returning `EFAULT`
("execl failed: Bad address") — exactly the crash PECAN's `torch_shm_manager` hit
(PyTorch's DataLoader workers `execl()` this helper binary to set up shared-memory tensor
passing whenever `num_workers>0`).

**Fix applied** in a new local branch (`fix/execl-execlp-va-list-corruption`) of the
session's `dftracer` clone (`<WS>/tmp/dftracer_src`, currently local-only — not pushed
upstream): rebuild a real `NULL`-terminated `argv[]` by walking the `va_list` with
`va_arg` in a loop, then dispatch through the array-based real function (`execv`/`execvp`)
instead of re-splicing the `va_list` into another `...` call — mirroring the pattern the
file already used correctly for `execv` itself. Rebuilt dftracer (MPI+HDF5+HIP all still
enabled) from this patched source.

**VALIDATED**: re-ran the exact same 16-rank DDP config that crashed
(`DFTRACER_DATA_DIR=all`, `num_workers=2`, fork-based DataLoader workers) — completed
cleanly this time, all 16 ranks trained a full epoch and finalized dftracer without any
`torch_shm_manager`/`execl` errors. This is a genuine upstream bug fix, not a workaround,
and should be proposed as a PR to `LLNL/dftracer` (not done this session — local branch
only, needs the user's explicit go-ahead to push/open a PR against an external repo).

**Also confirmed** (same finding as before the DATA_DIR=all crash was introduced):
`dftracer_service`'s per-hostname `.pid` file fix (from the `3e6fc42` develop pull) is
real, but full `start`+`stop` end-to-end validation was blocked by a SEPARATE,
pre-existing issue — `dftracer_service` still defaults to exclusive full-node
reservation even without an `--exclusive` flag, so a second `stop` job can't co-schedule
onto the already-running service's nodes. Had to `flux cancel` the service job directly.
Worth a dedicated retest with a deliberately larger/idle allocation in a future session.

## STEP 8 FOLLOW-UP (final): clean baseline + dftracer_service run, both fixes validated together (2026-07-20)

Full clean run combining everything fixed this session:
- Patched dftracer (execl/execlp va_list fix + latest develop pid-file fix), MPI+HDF5+HIP
  all enabled.
- `dftracer_service` launched with **`flux run -N4 -n4 -c1 dftracer_service start <dir>`**
  (explicit `-n`/`-c` instead of `--tasks-per-node`) — this avoided the previous
  `"exclusive": true` default (confirmed via `flux job info <id> jobspec`), letting it
  co-locate with the training job on the same 4 nodes instead of reserving them
  exclusively. **This is the actual fix for the "stop can't co-schedule" issue from
  earlier this session** — not a dftracer bug, a flux invocation pattern issue.
- 16-rank DDP training (`baseline3`, 4N x 4GPU, `DFTRACER_DATA_DIR=all`,
  `DFTRACER_INC_METADATA=1`, 2 epochs, same PDBspheres_v2_split8/150-shard config as all
  prior runs) completed cleanly: 561MB / 80 trace files, all 16 ranks
  `dftracer initialized`/`dftracer finalized` without error, loss converged normally
  (~similar trajectory to `baseline`/`baseline2`).
- `dftracer_service stop` (same `-N4 -n4 -c1` pattern, targeting the same 4 nodes) worked
  cleanly this time: **"Sent SIGINT to server (PID ...)" on all 4 nodes, zero "No running
  server found" errors** — confirms the per-hostname `.pid` file fix from the
  `3e6fc42` develop pull is real and complete, once combined with the correct
  non-exclusive `flux run` invocation.

**Updated recipe for future sessions** (supersedes the earlier `--tasks-per-node 1`
pattern that silently defaulted to exclusive):
```bash
flux run -N<n> -n<n> -c1 dftracer_service start <dir>   # one task/node, 1 core each, NOT exclusive
flux run -N<n> -n<n> -c1 dftracer_service stop <dir>    # same pattern to stop
```

## STEP 8 FOLLOW-UP (final, final): clean run with PyTorch Profiler enabled (2026-07-20)

Repeated the clean combined run (`baseline4`) with `DFTRACER_TORCH_PROFILE=1` added on
top of everything else validated (`execl`/`execlp` fix, `DFTRACER_DATA_DIR=all`,
`dftracer_service` co-located via `-n<n> -c1`). Fully successful:

- 16-rank DDP training, 2 epochs, completed cleanly (564MB / 80 trace files).
- `dftracer_service` started and stopped cleanly (SIGINT sent to all 4 nodes' servers,
  zero errors) — this is the SECOND consecutive clean start+stop, confirming the fix is
  reliable, not a one-off.
- Real category breakdown (sample of 20/80 files): HDF5 16.7M, POSIX 1.4M,
  **PP (PyTorch Profiler, GPU kernel-level) 131,556 events**, STDIO 28,128,
  preprocess 19,584, communication-io 6,534, compute 2,448,
  communication-except-io 1,224.

This is the most complete, feature-full trace collected this session: native dftracer
POSIX/STDIO/HDF5/compute/comm tracing + PyTorch Profiler GPU-kernel tracing +ic
node-level service counters, all in one clean run, with the execl/execlp upstream bug
fixed. A good candidate baseline for the next session's deeper compute-optimization work
(AMP bf16 validation, etc.) since it has the richest available data.

## HIP tracing refinement: PAGE_MIGRATION is a real HIP category, and it DOES fire under MPI

Correction/refinement to the earlier "HIP tracing broken by ANY MPI init" finding:
`src/dftracer/core/function/hip/intercept.cpp` registers FIVE separate rocprofiler-sdk
buffer-tracing services (`ROCPROFILER_BUFFER_TRACING_{HIP_RUNTIME_API,KERNEL_DISPATCH,
MEMORY_COPY,SCRATCH_MEMORY,PAGE_MIGRATION}`), each producing its own `cat` value in the
trace via `rocprofiler_query_buffer_tracing_kind_name()` (a dynamic lookup, not a
hardcoded string — confirms `PAGE_MIGRATION` in the trace IS the HIP layer, not a
separate/different mechanism).

In every real PECAN MPI/DDP run this session, **`PAGE_MIGRATION` DID produce events
(240 in the `baseline5` run)** while the other four (`HIP_RUNTIME_API`,
`KERNEL_DISPATCH`, `MEMORY_COPY`, `SCRATCH_MEMORY`) produced ZERO. This narrows the
root cause: it is not "rocprofiler-sdk registration fails entirely under MPI" — it is
that FOUR of the five buffer-tracing services fail to register/fire, while
`PAGE_MIGRATION` succeeds. `PAGE_MIGRATION` events originate from the KFD
(kernel driver) unified-memory page-fault/migration path, which is a different
subsystem than the HIP-runtime-API interception the other four depend on — consistent
with a registration-timing race specifically in the HIP-runtime-API hook path (the one
most exposed to an early `MPI_Init`/GTL touch), while the kernel-driver-level page-fault
instrumentation is registered through a separate, apparently unaffected path.

Not fully root-caused further this session — but this is a much more precise
"which of the five HIP buffer-tracing kinds actually work under MPI" answer for a
future investigation, rather than an "HIP is entirely broken" conclusion.
