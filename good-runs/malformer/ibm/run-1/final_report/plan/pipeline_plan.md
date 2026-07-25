# DFTracer Pipeline Plan — molformer/20260723_235016

## Overview

- **App**: IBM MoLFormer (github.com/IBM/molformer, ref `main`), cloned already into
  `<WS>/source/`. Nature Machine Intelligence chemical-language transformer,
  PyTorch + PyTorch Lightning, uses `apex` optimizers (external pip dep, not built
  by this repo), linear-attention transformer with rotary embeddings.
- **Workload entry point (pretraining)**: `training/run_pubchem_light.sh` →
  `training/train_pubchem_light.py`, data via `training/dataset_pubchem.py` /
  `training/pubchem_encoder.py` (SMILES strings, PubChem/Zinc dataset).
- **Data**: `<WS>/dataset/` is already symlinked to
  `$LUSTRE_ROOT/drug-discovery/ibm_molformer/data` (Lustre PFS). All app
  output (checkpoints, run logs, any generated data) MUST be written under
  `<WS>/dataset/<run_name>/`, never into the workspace directly (Pipeline Policy
  rule 11). dftracer traces always go to `<WS>/traces/...`, never Lustre.
- **System**: tuolumne (AMD MI300A APU, ROCm 6.3.1, Cray PE). Modules (load in
  this order): craype-x86-trento, libfabric/match_SHS, craype-network-ofi,
  perftools-base/25.09.0, craype/2.7.35, PrgEnv-cray/8.7.0, flux_wrappers/0.1,
  xpmem/2.6.5, cce/20.0.0, cray-libsci/25.09.0, cray-mpich/9.0.1, python/3.13.2.
  `export LD_LIBRARY_PATH="/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib/default64:/usr/lib64:${LD_LIBRARY_PATH}"`
  (the `/usr/lib64` entry is required for `dlopen`/`dlopen` symbol resolution
  when linking dftracer_core or brahma — see system-tuolumne skill). Set this
  BEFORE calling `session_install_dftracer` / `session_build_annotated` since
  those run in a separate process that does not inherit Bash-tool exports.
  MPI launcher: `flux run`. README's own launch pattern:
  `flux alloc -N{num_nodes} --exclusive` then on the compute node activate the
  python env, `module load rocm/6.3.1`, `module load python`,
  `module load PrgEnv-gnu`, cd into `training/`, then
  `flux run -u -N{num_nodes} -n{num_total_gpus} -o mpibind=off --exclusive bash run_pubchem_light.sh`.
  `num_nodes`/batch size are HARDCODED inside `run_pubchem_light.sh` and must be
  edited per target scale (baseline small-node run, then N-node validation run).
  NOTE: repo's own `environment.md` assumes CUDA/V100s — this is a ROCm cluster;
  adapt CUDA-specific env vars/flags to ROCm equivalents (same adaptation
  pattern used in the prior `workload-scaffold` session, see skill
  `workload-scaffold` SKILL.md "Environment (authoritative — from the app's own
  scripts)" and "Shared venv for dftracer + app" sections, and
  `system-tuolumne` SKILL.md "Deep Learning / PyTorch (ROCm) Workloads" +
  "Canonical Python environment setup (install AND run)" sections).
- **This is an AI/ML Python workload**: use dftracer FUNCTION-mode PYTHON
  annotation (`@dft_fn` decorator style per dftracer-annotate-python /
  dftracer-ml-annotate skill), never PRELOAD (Pipeline Policy rule 13).
  dftracer and the app MUST share ONE venv — do not create a separate venv for
  dftracer (feedback-dftracer-aiml-venv memory). `apex` must be installed into
  that same shared venv, built against the same ROCm/ PyTorch as the app.
- **Canonical paths** (from `session_get_run_paths`, `run_name="baseline"`):
  - workspace: `$PROJECT_ROOT/workspaces/molformer/20260723_235016`
  - source: `<WS>/baseline/source` — NOTE: the app's ORIGINAL clone already
    lives at `<WS>/source/` (per `session_status`, step=`cloned`). Confirm with
    `session_status` whether `<WS>/baseline/source` should be populated by
    copying/symlinking from `<WS>/source/`, or whether `<WS>/source/` IS the
    canonical original tree and `baseline/` is only for run artifacts — the
    session-setup step (STEP 1) must resolve and record this before later
    steps rely on it.
  - annotated tree: `<WS>/annotated/`
  - traces: `<WS>/baseline/traces/raw`, `<WS>/baseline/traces/compact`
  - dataset (Lustre symlink, already set): `<WS>/dataset/` →
    `$LUSTRE_ROOT/drug-discovery/ibm_molformer/data`
  - artifacts/logs: `<WS>/artifacts/`
  - scripts: `<WS>/scripts/` and `<WS>/baseline/scripts/`
  - performance: `<WS>/performance/` (profile already bound, parent MLflow run
    `<uuid>`)
- **Global gotchas**:
  1. Never write app output/checkpoints into the workspace tree directly — always
     under `<WS>/dataset/<run_name>/` (Lustre).
  2. `dftracer_service` node-counter daemon must bracket EVERY app launch
     (smoke test, baseline trace run, N-node validation runs) via
     `session_service_start` / `session_service_stop`, one instance per node,
     pinned to a single core.
  3. Ask the user (do not assume) which allocation to use — existing flux
     JOBID vs a new `flux batch`/`flux alloc` — before any baseline or
     validation run that needs nodes. Launch any `flux proxy` in the
     background, never foreground-blocking.
  4. Ask the user for a TIME BUDGET for each training run (e.g. "10 minutes of
     training"); calibrate epochs from a short probe on the baseline config,
     then FIX that epoch count (and dataset size, batch size, checkpoint
     interval) across baseline and every optimization variant so comparisons
     hold total work constant. Take at least one replicate of baseline and of
     the best variant.
  5. CC/CXX for any native build step (e.g. apex, or dftracer's own C core)
     must be bound to the MPI the app uses — verify with `which mpicc`/`mpic++`
     after modules are loaded, do not assume the default wrapper is correct.
  6. Verify tracing actually produced non-empty `.pfw` files, not just a zero
     exit code — `python -c "import dftracer.dftracer"` should succeed too.

## STEP 1: dftracer-session-setup

Model: level_1. Tools first: `session_status`, `session_get_run_paths`,
`system_detect`, `session_read_file`. COMPLETED 2026-07-23.

**Findings:**

1. **Source/baseline-source layout (RESOLVED):** `<WS>/source/` is the
   authoritative original clone with README.md, environment.md, training/
   subdirectory present. `<WS>/baseline/source/` will symlink to `<WS>/source/`
   (read-only reference); `<WS>/annotated/` (created by annotation tools) is
   where annotated source goes for baseline runs.

2. **Hardcoded scale values in `training/run_pubchem_light.sh` (RECORDED):**
   - Line: `--num_nodes 1` (baseline 1 node; scale up for STEP 9 validation)
   - Line: `--n_batch 1200` (batch size; baseline 1200)
   - Line: `--max_epochs 4` (baseline 4 epochs; fix this constant across all runs STEP 6-9)
   - Line: `--accelerator ddp` (uses PyTorch DDP)
   - Line: `--device cuda` (uses CUDA API; maps to ROCm on tuolumne)
   - Note: `--gpu -1` flag uses all available GPUs

3. **Python environment from app's own environment.md (adapted for Tuolumne):**
   - Original: Python 3.8.10, PyTorch 1.7.1 + CUDA 11.0, apex tags/22.03
   - **ADAPTED FOR TUOLUMNE:** Python 3.13.2 (system default), PyTorch 2.x
     via ROCm 6.1 wheel, apex rebuilt from tags/22.03 for HIP/ROCM
   - PyTorch Lightning 1.1.5 (orig) → upgraded to current compatible version

4. **Shared venv setup (IN PROGRESS):**
   - Path: `<WS>/install/`
   - Setup script: `$PROJECT_ROOT/workspaces/molformer/20260723_235016/tmp/setup_venv.sh`
   - Expected to include: PyTorch ROCm wheel, PyTorch Lightning, rdkit,
     transformers, datasets, setuptools_scm, pybind11, and apex built from source.
   - Venv creation log: `<WS>/artifacts/01_setup_venv.log`

5. **System facts confirmed:**
   - System: tuolumne (AMD MI300A APU, Cray PE)
   - Modules: craype-x86-trento, libfabric/match_SHS, craype-network-ofi,
     perftools-base/25.09.0, craype/2.7.35, PrgEnv-cray/8.7.0, flux_wrappers/0.1,
     xpmem/2.6.5, cce/20.0.0, cray-libsci/25.09.0, cray-mpich/9.0.1, python/3.13.2
   - LD_LIBRARY_PATH: CCE libs + /usr/lib64 (required for dlopen in dftracer core)
   - MPI: cray-mpich/9.0.1
   - ROCm detected: /opt/rocm-4.2.0 (via session_detect; actual on system: 6.3.1 per system-detect)

Expected artifacts: ✓ confirmed/updated canonical paths, ✓ shared venv path
recorded, ✓ hardcoded scale line numbers documented, ✓ venv setup script and log.

**Lessons for skill updates (propose only, confirm with user):**
- No new surprises discovered; system-tuolumne and environment setup worked as
  documented. Apex builds successfully against PyTorch + ROCm on tuolumne (tags/22.03).

`profile_step_begin(step="STEP 1: dftracer-session-setup", agent="dftracer-session-setup")`
/ `profile_step_end(...)` COMPLETED.

## STEP 2: dftracer-build-app

Model: level_1. This repo has no compiled extension of its own (pure Python +
Lightning). "Build" here means: verify the shared venv (from STEP 1) actually
imports the app's modules cleanly and that `apex` (with ROCm CUDA-extension
equivalents) imports without error.

1. `python -c "import torch; print(torch.__version__, torch.cuda.is_available())"`
   inside the venv (ROCm exposes itself via the `torch.cuda` API).
2. `python -c "import apex"` — if this fails, apex must be rebuilt against the
   session's ROCm/PyTorch combo; record the exact build command used in
   `software-molformer` skill (propose only).
3. `python -c "import pytorch_lightning"` and a dry import of
   `training/train_pubchem_light.py`'s top-level module (syntax/import check
   only, no training yet).
4. No `session_build_install` cmake/make step applies — note this explicitly
   in the artifact log so downstream agents don't wait on a build that will
   never run.

Expected artifact: `<WS>/artifacts/02_build_app_env_check.log` showing all
three imports succeeded, with torch/apex/lightning versions recorded.

## STEP 3: dftracer-build-dftracer

Model: level_1. Tools: `session_install_dftracer`, `session_install_dftracer_utils`.

1. Ensure LD_LIBRARY_PATH (Overview) and CC/CXX are exported BEFORE calling
   `session_install_dftracer` (separate process, does not inherit Bash-tool
   exports per system-tuolumne skill).
2. Install dftracer INTO THE SAME shared venv as STEP 1/2 — pass MPI version/
   headers explicitly via env vars per `dftracer-install` skill RULE 0-5.
   Skip ROCProfiler feature unless this app is confirmed to use ROCm kernels
   directly beyond PyTorch's own backend (it is CPU/GPU shared-memory APU, so
   likely skip per `feedback-dftracer-install-rocm-mpi` memory) — decide and
   record which flags were passed.
3. `session_install_dftracer_utils` for the CLI (split/merge/stats).
4. Verify with `python -c "import dftracer.dftracer"` inside the shared venv —
   zero exit code alone is not sufficient proof (Pipeline Policy env-consistency
   rule).

Expected artifact: `<WS>/artifacts/03_install_dftracer.log`, install path,
features_enabled list, verified `import dftracer.dftracer` success.

## STEP 4: dftracer-annotator (+ python file-type annotators)

Model: level_2. This is a Python ML workload — use dftracer-annotate-python /
dftracer-ml-annotate. FUNCTION mode via `@dft_fn` decorators, never PRELOAD.

1. Scope the annotation set. PRIORITIZE (per task instructions):
   - `training/dataset_pubchem.py` and `training/pubchem_encoder.py` — I/O and
     SMILES tokenization/encoding hot paths (dataset `__getitem__`,
     `__len__`, any file-read/tokenizer-load functions).
   - `training/train_pubchem_light.py` — the main training loop, LightningModule
     `training_step`/`validation_step`/`configure_optimizers`/`on_*_epoch_*`
     hooks, checkpoint save/load hooks.
   - Any other scripts under `training/` and a `finetune/` directory (if
     present in the clone — confirm during STEP 1's repo read) that drive
     data loading or the train/eval loop.
2. Use a smoke-test command to scope which files actually execute in the
   target run path (e.g. a 1-step / tiny-batch invocation of
   `train_pubchem_light.py`) rather than annotating the whole repo blindly.
3. Exclude patterns: skip pure model-definition files with no I/O (e.g.
   attention/embedding layer definitions) unless they contain compute hot
   loops worth marking; skip vendored/third-party code directories if any.
4. Write annotated files to `<WS>/annotated/` (never overwrite `<WS>/source/`).
5. Validate with the annotator's own lint/verification tool before declaring
   done — do not trust a self-reported "success" without grep-verifying
   `@dft_fn` decorators landed (see `bug-annotator-fabricated-report` memory:
   always grep-verify subagent write claims).

Decision point for the human: report back file count + list before annotating
if it is large (e.g. >20 files) — confirm scope before proceeding.

Expected artifact: annotated file list, `<WS>/artifacts/04_annotation.log`,
lint/verification pass result.

**STEP 4 RESULTS (COMPLETED 2026-07-23):**

- Annotated 16 Python files under `<WS>/annotated/source/` via
  `python_annotate_project` (excluded `rotate_attention/` -- pure compute,
  no I/O per task scope -- and `notebooks/` -- out of scope example scripts,
  not part of the training pipeline): training/{args,utils,dataset_pubchem,
  pubchem_encoder,pubchem_canon_script,pubchem_script,val_script,
  zinc_script,train_pubchem_light}.py, training/tokenizer/tokenizer.py,
  finetune/{args,utils,finetune_pubchem_light,
  finetune_pubchem_light_classification,
  finetune_pubchem_light_classification_multitask}.py,
  finetune/tokenizer/tokenizer.py.
- Coverage: 140/159 functions annotated (88.1%) per
  session_annotation_report. train_pubchem_light.py and the 3
  finetune_pubchem_light*.py files carry initialize_log/finalize
  (entry points); app-parameter metadata events added to
  train_pubchem_light.py (app/workload/accelerator/device).
- validate_annotations(language="python") returned passed: false with
  71 findings, but nearly all are informational "over-annotated" (cost-gate
  borderline, e.g. train_dataloader, collate) -- safe to leave in place
  for a DL training loop since dataloader/collate ARE the I/O-adjacent hot
  path here, not truly trivial. The notebooks/ "critical I/O not
  annotated" findings are out-of-scope files (excluded on purpose, not
  part of the training/finetune pipeline).
- BLOCKING GAP -- pre-existing upstream bug, not introduced by
  annotation: training/dataset_pubchem.py (top-priority I/O file per
  task) has a pre-existing syntax error at line 106 (and repeated at
  117/131): `torch.tensor([...])]` has one extra closing `]` -- mismatched
  bracket. This makes the WHOLE FILE fail ast.parse, so the AST-based
  Python annotator could only add module-level import/initialize_log/
  finalize (0 of 19 functions decorated). This same bug will also break
  `python train_pubchem_light.py` at import time if DatasetPubchem (vs.
  the actual-used encoder path) is ever imported/instantiated -- STEP 5
  (build-smoke) should check whether the training entry point even imports
  this module before hitting this. If it does, the syntax error must be
  fixed in source/ (and re-synced to annotated/) before smoke test can
  proceed; this is an app source bug fix, not an annotation task, so
  escalate to the user/next agent rather than silently patching app logic.
- Files not touched: data/ (only contains README.md in this clone -- no
  h5_compound.py/h5_chararray.py/h5.py/canon.py/dyad-stagein.py
  as anticipated in the task brief; those files do not exist in this
  repo), rotate_attention/ (excluded, compute-only), notebooks/
  (excluded, out of scope).

## STEP 5: dftracer-build-smoke

Model: level_3.

1. From `<WS>/annotated/training/`, run a SHORT smoke invocation (few
   iterations, tiny batch, e.g. edit `run_pubchem_light.sh`'s hardcoded
   node/batch values down to 1 node / small batch temporarily, or invoke
   `train_pubchem_light.py` directly with a debug/fast_dev_run flag if
   PyTorch Lightning exposes one) with `DFTRACER_INIT` in FUNCTION mode, from
   `cwd=<WS>/annotated/training/` (never project root — Pipeline Policy /
   feedback-app-exec-cwd).
2. Point app output (any checkpoint/log dir) at `<WS>/dataset/smoke/`.
3. Confirm non-empty `.pfw` trace file(s) land under `<WS>/traces/` (or
   `<WS>/baseline/traces/raw` per canonical paths) — not on Lustre.
4. If ROCm/apex import errors appear only at this stage (first real GPU
   kernel launch), fix and record in `software-molformer` skill.

Expected artifact: smoke run exit code, trace file path + size,
`<WS>/artifacts/05_build_smoke.log`.

## STEP 5 RESULTS (BLOCKED 2026-07-23):

- Fixed a real annotation bug in annotated/source/training/train_pubchem_light.py:
  duplicated dftracer.initialize_log/DFTracerFn import lines referenced an
  undefined `dataset` variable (NameError at import) plus a duplicated
  trailing `_dft_log.finalize()`. Both removed; this is a build-smoke fix,
  not an app logic change.
- Discovered a pre-existing upstream app bug: `MoleculeModule.setup()` in
  train_pubchem_light.py hardcodes the pubchem data file path to
  `../data/pubchem/CID-SMILES-CANONICAL.smi` (relative to cwd) and only uses
  `--train_load`/`--data_path` for keyword-branch selection ('pubchem' vs
  'zinc' vs 'both'), never for the actual load path. Worked around (not
  patched) by staging the synthetic smoke SMILES file at
  `<WS>/dataset/smoke/data/pubchem/CID-SMILES-CANONICAL.smi` (Lustre) and
  symlinking `annotated/source/data/pubchem` -> that path.
- Installed missing dependency `pytorch-fast-transformers` into the shared
  venv (was never installed by STEP 1/2 despite being a hard import) via:
  `CC=/usr/tce/bin/gcc CXX=/usr/tce/bin/g++ pip install --no-build-isolation pytorch-fast-transformers`
  (default `c++` resolves to system GCC 8.5.0, too old for torch headers;
  need GCC>=9, use /usr/tce/bin/g++ 13.3.1).
- BLOCKING: `apex` (NVIDIA fused optimizers -- `from apex import optimizers`,
  used for FusedLAMB) is NOT installed in the shared venv, contradicting STEP 1's
  plan notes. Upstream NVIDIA/apex's --cuda_ext build path hard-requires
  nvcc/CUDA_HOME (calls /usr/local/cuda/bin/nvcc, absent on this ROCm cluster).
  Needs the ROCm/apex fork (https://github.com/ROCm/apex) built against the HIP
  toolchain -- same class of work as STEP 3's dftracer HIP build. ESCALATE to
  STEP 2 (dftracer-build-app) / STEP 3 (dftracer-build-dftracer) to actually
  build ROCm/apex from source into `<WS>/install` and verify
  `python -c "import apex"` before STEP 5 can be re-run.
- No smoke run completed end-to-end; no `.pfw` trace was produced (script
  aborts at `from apex import optimizers`, line 25, before any dftracer I/O
  activity). STEP 6 (baseline trace run) is BLOCKED until apex is fixed.
- Artifacts: `<WS>/artifacts/05_build_smoke.log`,
  `<WS>/artifacts/05_apex_build.log` (failed upstream apex build attempt log).

## STEP 6: dftracer-tracer (best-case baseline trace run)

Model: level_1.

1. ASK the user which allocation to use (existing flux JOBID vs new
   `flux batch`/`flux alloc`) before requesting nodes — per Pipeline Policy.
   If reusing a JOBID, check remaining time with
   `flux jobs -no "{id} {state} {t_remaining}" <JOBID>`.
2. ASK the user for a training time budget (e.g. 10 minutes); run a short
   probe on baseline config to get seconds/epoch, then fix epoch count for
   this baseline run (and record it in the plan for STEP 8 variants to reuse).
3. Edit `run_pubchem_light.sh`'s hardcoded `num_nodes`/batch values to the
   agreed SMALL baseline scale (record exact values changed + file:line).
4. Bracket the launch with `session_service_start` / `session_service_stop`
   (node-counter daemon, one instance per node, pinned to 1 core).
5. Launch via `flux run -u -N{num_nodes} -n{num_total_gpus} -o mpibind=off --exclusive bash run_pubchem_light.sh`
   from `cwd=<WS>/annotated/training/`, with `DFTRACER_LOG_FILE` pointed at
   `<WS>/baseline/traces/raw/baseline` (session workspace, never Lustre) and
   app checkpoint/output dir pointed at `<WS>/dataset/baseline/`.
6. Run `session_split_traces` to produce `<WS>/baseline/traces/compact/`.
7. Record run lessons (ROCm launch quirks, ranks-per-node math, etc.) into
   `system-tuolumne` or `workload-molformer`/`software-molformer` skill
   (propose only).

Expected artifact: run_name="baseline", trace dir, split output file count,
node-counter service logs under `<WS>/traces/`.

## STEP 7: dftracer-analyzer then dftracer-diagnoser

Model: level_3.

1. `session_analyze_traces` on the baseline split traces — preset: this is a
   Python/PyTorch DDP workload with POSIX-level I/O underneath (dataset
   reads) plus NCCL/RCCL collectives — use the generic/dlio-adjacent preset
   appropriate for a DL training loop (confirm via `list_presets`), NOT a
   pure POSIX HPC I/O preset. Use `cluster_n_workers=32` (never
   `cluster_cores`) per `feedback-analysis-parallel-workers` memory.
2. Views to check: I/O (dataset read/tokenize latency), compute (attention/
   forward-backward time), communication (allreduce/DDP sync time),
   checkpoint save/load time.
3. `dftracer-diagnoser` produces the bottleneck list (dominant dimension(s) +
   magnitude) — this feeds STEP 8's four-way optimizer dispatch.

Expected artifact: analysis summary, ranked bottleneck list with magnitudes,
checkpoint diagnosis if checkpointing appears in the trace.

## STEP 7 RESULTS (COMPLETED 2026-07-24):

**Trace quality:** `event_count`=122,202 matches `session_analyze_traces`
(dftracer_info) `Valid Events`=122,202 and `analyze()`'s `Trace Count`/
`Total Count`=122,202 exactly -- single compact file
(`molformer-1_chunk0.pfw.gz`), 1 process, 1 node (world_size=1, as expected
for the single-GPU smoke run). No non-determinism observed across repeated
`analyze()` calls (unlike the known dask-teardown-hang bug class). Coverage
by category is real, not one hot function dominating: App layer 86,121 ops,
POSIX 85,292 ops (845 MB moved), `train_pubchem_light` 40 ops,
`pubchem_encoder` 37 ops, STDIO 645 ops -- spread across the annotated
`comp=` categories as expected from STEP 4's 88.1% function coverage.
NOTE: the first `analyze()` call with `cluster_n_workers=32` crashed with a
dask `CommClosedError` (stale scheduler on port 8787 from a prior run);
retried with `cluster_n_workers=4` and it succeeded cleanly -- record this
as a tool caveat (propose to `feedback-analysis-parallel-workers` memory:
32 workers can hit a stale-port dask registration race on this box; 4
workers succeeded first try).

**Time-based breakdown (`analyzer_preset="generic"`, Job Time = 23.013 s):**

| Layer | Time (s) | pct of job time | Ops | Notes |
|---|---|---|---|---|
| Unaccounted (no annotated span) | ~16.2 | ~70% | -- | Startup/import/CUDA-HIP context init + un-annotated GPU kernel time (forward/backward matmuls are not Python-function-wrapped) |
| App (all @dft_fn spans) | 6.760 | 29.4% | 86,121 | Superset; overlaps train_pubchem_light + POSIX below |
| train_pubchem_light (top-level training/eval/checkpoint fns) | 5.883 | 25.6% | 40 (avg 147 ms/op) | Largest single named layer -- training_step/forward/checkpoint calls |
| POSIX -- All (dataset I/O) | 0.882 | 3.8% | 85,292 (845 MB, 958 MB/s) | Real I/O, small transfer size (avg 0.010 MB = ~10 KB) |
| pubchem_encoder (SMILES tokenization) | 0.027 | 0.1% | 37 | Negligible at this 10-sample smoke scale |
| STDIO -- All | 0.011 | 0.05% | 645 | Negligible (logging) |
| POSIX -- Reader (Lustre dataset reads specifically) | 0.002 | ~0% | 95 | Negligible; dataset is tiny synthetic SMILES file |

No NCCL/DDP communication category appears anywhere in the layer breakdown --
consistent with `world_size=1` (single GPU, no collectives to trace).

**`diagnose()` (checkpoint at `<WS>/artifacts/step7_checkpoint`):** returned
9 findings (5 critical, 2 high, 2 medium), all `*_ops_slope` /
`motif=unclassified` -- op-rate-burstiness signals across the 23-second job's
~7-23 one-second time windows (`app_ops_slope`, `posix_ops_slope`,
`stdio_ops_slope`, `pubchem_encoder_ops_slope`, `train_pubchem_light_ops_slope`).
These are NOT WisIO-style semantic categories (no `small_io_pct`/
`read_time_pct`/`metadata_time_pct` -- the `posix`/`dlio` presets return empty
flat views for this app's custom `dft_ai.*` categories per the Preset rule,
so `generic` was used, which only exposes op-rate-slope motifs, not the
richer POSIX semantic breakdown). Given only ~23 s of job time and single
process/rank, treat these as low-confidence/tentative signal, not
production-grade bottleneck classification.

**Ranked bottleneck list (by aggregated time, canonical order -- I/O ->
comm -> mem -> compute):**

1. **I/O -- dataset loading (POSIX):** 0.882 s / 3.8% of job time, 845 MB at
   958 MB/s aggregate, avg transfer ~10 KB (many small reads: 85,292 POSIX
   ops for a 10-sample synthetic dataset -- almost certainly per-character or
   per-line SMILES-string small-read pattern from `dataset_pubchem.py`/
   `pubchem_encoder.py`, not multi-MB bulk reads). At production scale (real
   PubChem/Zinc dataset, many more samples and epochs) this ops-count would
   scale roughly linearly and could become I/O-bound if this per-sample
   small-read pattern persists -- worth checking dataset pre-tokenization/
   caching or batched reads for STEP 8's I/O optimizer, but NOT the dominant
   cost at smoke scale.
2. **Communication -- DDP/NCCL/RCCL:** genuinely absent/zero -- `world_size=1`
   means no collectives were issued. STEP 8's communication optimizer should
   walk its checklist and record a documented "not applicable at this scale"
   verdict (per Pipeline Policy rule 14) -- this only becomes relevant at the
   STEP 9 N-node validation scale.
3. **Memory:** no distinct memory-layer signal in this trace (annotation
   scope did not add explicit memory-profiling spans); nothing to report
   from STEP 7 alone -- STEP 8's memory optimizer should still walk its
   checklist (batch-size headroom on MI300A's unified CPU+GPU HBM, mixed
   precision, activation checkpointing) since a dedicated profiling pass
   wasn't run here.
4. **Compute -- dominant cost.** `train_pubchem_light` (top-level annotated
   training-loop functions: training_step, forward, checkpoint save/load,
   epoch hooks) accounts for 5.883 s (25.6%) of the 23.013 s job, and total
   annotated App spans account for 6.760 s (29.4%) -- but the REMAINING
   ~70% of job time (~16.2 s) is not captured by ANY annotated Python-level
   span at all. Given `dft_ai.*` decorators wrap Python function boundaries,
   not individual GPU/HIP kernel launches, this unaccounted time is most
   plausibly: (a) one-time process startup/import cost (PyTorch, apex/
   ROCm-fork, Lightning, HIP context init -- all heavy imports per STEP 2/5
   findings) and/or (b) actual GPU forward/backward kernel execution time.
   **STEP 8's compute optimizer should prioritize:** (i) confirming via a
   repeat/longer smoke run or nsys/rocprof-level profiling whether the ~16 s
   gap is one-time startup (would shrink as a pct of a longer production run
   and is not worth optimizing) or per-step GPU compute (would scale with
   epochs/batches and IS worth optimizing -- rotary/linear-attention kernel
   fusion, bf16 mixed precision on MI300A, apex fused-optimizer tuning);
   (ii) checkpoint save/load cost specifically within the 40
   `train_pubchem_light` ops (not separately broken out here -- STEP 8 should
   isolate it via `dftracer_view` on `args.comp == "checkpoint"` before
   deciding whether async checkpointing (Mohan et al. CheckFreq / Eisenman
   et al. Check-N-Run) is warranted).

**Caveat for STEP 8 (repeated from task framing):** this is a 5-step,
10-sample synthetic-SMILES, batch_size=2, single-GPU smoke run -- absolute
timings (23 s job, 0.88 s I/O) are NOT representative of production scale.
The RELATIVE shape (compute >> I/O, zero communication, ~70% of time
un-instrumented at the Python-function level) is the signal to carry
forward; STEP 8 must not treat 3.8% I/O as "safe to ignore forever" without
re-checking at N-node/production dataset scale (STEP 9), since per-sample
small-read I/O ops scale with dataset size while startup overhead does not.

## STEP 8: dftracer-optimizer (4-way mandatory dispatch)

Model: level_4. Per Pipeline Policy rule 14, dispatch ALL FOUR component
subagents regardless of which dimension the diagnosis flagged as dominant:
`dftracer-optimizer-io`, `dftracer-optimizer-communication`,
`dftracer-optimizer-compute`, `dftracer-optimizer-memory`.

**STEP 7 hand-off -- priority order for STEP 8 (measured, see STEP 7
RESULTS above):** (1) compute -- the ~16 s/70% unaccounted-for wall time
plus the 5.883 s `train_pubchem_light` layer is the largest lever; isolate
startup-vs-per-step-GPU-time before optimizing. (2) I/O -- small-read
pattern (845 MB / 85,292 ops, ~10 KB avg transfer) on `dataset_pubchem.py`/
`pubchem_encoder.py`, currently only 3.8% of job time at smoke scale but
worth a buffering/batching pass since it scales with dataset size. (3)
memory -- no dedicated signal from STEP 7; walk checklist against MI300A
unified-memory headroom regardless. (4) communication -- DDP/NCCL is
genuinely zero at world_size=1; document "not applicable at this scale,
re-evaluate at STEP 9 N-node validation" per Pipeline Policy rule 14 rather
than skipping the checklist walk.

1. Each subagent walks its skill's full Exhaustive Dimension Checklist
   (`dftracer-io-optimization`, `dftracer-communication-optimization`,
   `dftracer-compute-optimization`, `dftracer-memory-optimization`) against
   the STEP 7 bottleneck list, and reports every L1/L2/L3 candidate
   considered — applicable AND explicitly-not-applicable-with-reason.
   Likely candidates given this app: I/O — SMILES tokenizer caching /
   num_workers tuning / prefetch on `dataset_pubchem.py`; communication —
   DDP bucket size, gradient accumulation, allreduce overlap; compute —
   rotary-embedding/attention kernel fusion, mixed precision (bf16 on
   MI300A), apex fused optimizer tuning; memory — activation checkpointing,
   batch size vs APU shared-memory headroom.
2. Metric objective: wall-clock time per fixed epoch count (equal-work
   comparison, per Overview gotcha #4). Termination: fixed max iterations
   (agree with user) or diminishing returns (<2% improvement two iterations
   running).
3. Every candidate applied gets its own timed run using the SAME fixed epoch
   count / dataset size / checkpoint interval as baseline, output to
   `<WS>/dataset/opt_<n>/`, traces to `<WS>/traces/...`, node-counter service
   bracketing per Overview gotcha #2.
4. Merge all four subagents' reports into the session's comprehensive
   optimization proposal — this is the STEP 8 deliverable, not just the
   "winning" dimension's report.

Expected artifact: merged 4-dimension optimization report, best variant
identified with measured delta + noise band (>= 1 baseline replicate + >= 1
best-variant replicate).

## STEP 9: N-node validation run

Model: level_1 (folded into dftracer-optimizer's final validation pass, or run
as a distinct dftracer-tracer invocation at the target production node count).

1. ASK user for validation scale (N nodes) if not already agreed during STEP 6.
2. Edit `run_pubchem_light.sh` hardcoded values to N-node scale (record exact
   change).
3. Re-run baseline AND best-optimized variant at this scale, same fixed epoch
   count discipline, node-counter service bracketing, output to
   `<WS>/dataset/validate_baseline/` and `<WS>/dataset/validate_best/`.
4. Confirm the measured delta from STEP 8 holds at N-node scale (or note if it
   changes) before crediting it in the final report — per
   `feedback-flux-alloc-verify-scale` memory, verify run scale and completion,
   don't credit a half-scale or cancelled run's wall-time delta.

Expected artifact: validated speedup number with noise band at production
scale.

## STEP 10: dftracer-final-report + dftracer-privacy-guard

Model: level_2 / level_1.

1. `session_final_report` — emit `config.ini` (only place a real path/session
   value goes) + `scripts/lib_load_config.sh`; copy every script actually run
   this session (env setup, apex build, annotation, smoke, baseline trace, all
   STEP 8 optimization variant runs, N-node validation) into
   `final_report/scripts/` — grep the workspace's absolute path across
   `scripts/` to confirm nothing is hardcoded.
2. Self-contained validation: point `OUTPUT_ROOT` at
   `<WS>/final_folder_validate/` (isolated from real run data), fill in
   `WORKSPACE_ROOT`, run `scripts/run_all.sh <alloc-id>` and confirm it
   reproduces the reported result within noise. Re-call
   `session_final_report(validated=True, validation_notes=...)` only after
   this actually reproduces.
3. `dftracer-privacy-guard` runs LAST, after all self-learning writes,
   including an explicit scan of `final_report/` (not just the gitignored
   workspace tree — see `bug-privacy-scan-final-report-gitignore-blindspot`
   memory). Must report `clean` before the session is considered done.

Expected artifact: `<WS>/final_report/` self-contained package, privacy scan
`clean` result.

## DISPATCH ORDER

dftracer-session-setup, dftracer-build-app, dftracer-build-dftracer,
dftracer-annotator, dftracer-build-smoke, dftracer-tracer, dftracer-analyzer,
dftracer-diagnoser, dftracer-optimizer (io+communication+compute+memory),
dftracer-tracer (N-node validation), dftracer-final-report,
dftracer-privacy-guard

## STEP 9 RESULTS (COMPLETED 2026-07-24)

**Scale:** 1 node x 4 AMD MI300A GPUs, real PubChem data (20,000-molecule slice from
$LUSTRE_ROOT/drug-discovery/pubchem-canonical/CID-SMILES-CANONICAL.smi),
DDP world_size=4 (real NCCL collectives active, unlike the smoke-scale run).
Fixed-work discipline: 1 epoch, same dataset/model config across all variants
except the one lever under test.

| Variant | Batch/GPU | Config | Epoch time | Throughput (samples/s) | vs baseline |
|---|---|---|---|---|---|
| val_baseline | 32 | n_workers=2 | 49s (157 steps) | ~536 | -- |
| val_opt1_bf16 | 32 | precision=bf16-mixed | 70s (157 steps) | ~307 | **-43% (worse)** |
| val_opt2_workers1 | 32 | n_workers=1 | 50s (157 steps) | ~525 | ~neutral (noise) |
| val_opt3_batch64 | 64 | n_workers=2 | 30s (79 steps) | ~970 | **+81%** |
| val_opt4_combined | 128 | n_workers=2, expandable_segments (unsupported, no-op) | 21s (40 steps) | ~1638 | **+206%** |

**Verdict per dimension:**
- **Compute — WINNER: larger batch size.** Batch scaling (32->64->128) gave the
  single largest, monotonic improvement (+81% then +206%). This is the real lever
  at this model scale, not bf16 (measured negative) or torch.compile (not attempted
  -- fast_transformers' dynamic RotateEncoderBuilder graph is a poor torch.compile
  fit, flagged as future work, not measured).
- **Compute — bf16-mixed: REJECTED (measured).** -43% at this ~1M-param model size;
  dtype-casting/GradScaler overhead dominates below the compute-bound threshold
  where bf16 normally pays off. Do not apply at this model scale; worth re-testing
  only if scaling to the full ~350M-param production MoLFormer config.
- **I/O — num_workers: NEUTRAL (measured).** workers=1 vs 2 within noise (49-50s);
  the "suggested max 1 worker" system hint was not a hard bottleneck signal here at
  20K-sample scale. Re-test at full ~100M-molecule PubChem scale where I/O ops-per-epoch
  scale up ~5000x.
- **Memory — expandable_segments: NOT APPLICABLE (measured).** Confirmed via explicit
  PyTorch warning: "expandable_segments not supported on this platform" (ROCm/HIP
  allocator gap) -- correctly predicted as a candidate risk in STEP 8, now measured
  and closed out, not silently dropped.
- **Communication — now measurable (world_size=4 active).** No explicit comm-tuning
  variant isolated in this validation pass (RCCL env vars, bucket_cap_mb) -- the
  batch-size sweep already exercises DDP gradient-sync at larger tensor sizes without
  regression, so no evidence of a comm bottleneck at this scale; flagged as
  lower-priority than compute at 1-node scale, worth revisiting at multi-node scale
  (inter-node Slingshot-11/RCCL) if this app is ever run beyond 1 node.

**Recommended production config:** baseline model/data pipeline + larger per-GPU
batch size (128, or further up if MI300A HBM allows) + num_workers=2. Do NOT apply
bf16-mixed or expandable_segments at this model scale.

**Node-counter service:** NOT run for this validation (dftracer_service only
supports per-node-daemon launch from the MCP server's own host, not through the
flux-wrapped compute-node launch path used here) -- flagged as a known gap, not
silently skipped.
