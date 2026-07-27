# DFTracer Pipeline Plan — megatron_deepspeed/20260726_185517

## Overview

- **App:** Megatron-DeepSpeed (argonne-lcf fork), `https://github.com/argonne-lcf/Megatron-DeepSpeed`, ref `main`.
- **Workspace:** `/usr/WS2/haridev/dftracer-agents/workspaces/megatron_deepspeed/20260726_185517`
  - `baseline/source` — cloned app source (695 files), already present, step="configured".
  - `annotated/` — copy for instrumentation (695 files, already cloned; NOT yet annotated).
  - `build/`, `install/`, `artifacts/`, `scripts/`, `performance/`, `tmp/` all exist.
  - `dataset_path` (PFS symlink target): `/p/lustre5/haridev/workspaces/megatron-deepspeed` — ALL app data
    (Megatron indexed datasets, checkpoints, DeepSpeed run outputs) MUST live here or under
    `<WS>/dataset/<run_name>/` symlinked to it. dftracer TRACES always stay under `<WS>/traces/...`
    or `<run>/traces/raw|compact`, never on Lustre.
- **System:** Tuolumne (AMD MI300A APU, Cray PE, ROCm). `sudo` unavailable. MPI launcher: `flux run -n <N>`.
  - Modules (load in this order): craype-x86-trento, libfabric/match_SHS, craype-network-ofi,
    perftools-base/25.09.0, craype/2.7.35, PrgEnv-cray/8.7.0, flux_wrappers/0.1, xpmem/2.6.5,
    cce/20.0.0, cray-libsci/25.09.0, cray-mpich/9.0.1, python/3.13.2.
  - REQUIRED before `session_install_dftracer` / `session_build_annotated` (separate process, does not
    inherit Bash exports):
    `export LD_LIBRARY_PATH="/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib/default64:/usr/lib64:${LD_LIBRARY_PATH}"`
    (missing `/usr/lib64` -> `ld.lld: undefined reference: dlopen`).
  - MPI: cray-mpich 9.0.1, craymich compatible, mpicc=`/opt/cray/pe/mpich/9.0.1/ofi/crayclang/20.0/bin/mpicc`,
    mpicxx=`/opt/cray/pe/mpich/9.0.1/ofi/crayclang/20.0/bin/mpicxx`. Bind `CC`/`CXX` to these, not a bare `which mpicc`.
  - HDF5 system: 1.10.5 at `/usr` — this app is NOT HDF5-IO-bound (PyTorch DDP training loop like
    software-molformer/software-pecan/workload-scaffold); dftracer HDF5 tracing is optional/low priority,
    keep `DFTRACER_ENABLE_HDF5=ON` for parity but do not expect HDF5 as a bottleneck.
  - ROCm detected: `/opt/rocm-4.2.0` (old) — resolve actual PyTorch-wheel-compatible ROCm module during
    the dftracer/app build step; PyTorch wheel bundles CUDA 13.0.3 (HIP-visible-as-CUDA runtime) —
    verify `torch.version.hip` / `torch.cuda.is_available()` matches the loaded ROCm module per
    software-molformer's "ROCm/PyTorch wheel version must match the loaded ROCm module exactly" lesson.
  - `hip_tracing_needed: false` per system_detect — do NOT enable `DFTRACER_ENABLE_HIP_TRACING` unless the
    app's own C/C++ ops are found using HIP directly (check DeepSpeed's custom op build, not just ROCm presence).
- **Allocation:** existing Flux allocation, job id `f3NYii2RimCB`, pdebug queue, 16 nodes (tuolumne[1033-1048]).
  Use `flux proxy f3NYii2RimCB bash <wrapper>.sh ...` launched with `run_in_background: true` (never block
  the foreground on a long proxy). Check remaining time first:
  `flux jobs -no "{id} {state} {t_remaining}" f3NYii2RimCB`.
  Use only 1-2 nodes of the 16 for smoke test / baseline trace (BASELINE run_name below); do not consume the
  whole allocation on debug work.
- **Target workload:** GPT pretraining, ~345M params, GPT ONLY (no BERT this pass).
  Script: `examples_deepspeed/rebase/ds_pretrain_gpt_125M.sh` (despite the filename, it is a template with
  commented model-size blocks). Uncomment the **"GPT-3 Medium 350M"** block
  (`model_size=0.35 num_layers=24 hidden_size=1024 num_attn_heads=16 global_batch_size=256 lr=3.0e-4
  min_lr=1.0e-6 init_std=0.018`) and comment out the default 125M block — this is the closest in-repo
  config to the requested ~345M and needs no new config authored.
  DS config template: `examples_deepspeed/rebase/ds_config_gpt_TEMPLATE.json`.
  Launcher note: the script invokes `deepspeed pretrain_gpt.py ...` using DeepSpeed's own multi-node
  launcher (pdsh/mpirun-based), which does NOT map directly to `flux run`. STEP 5 (build+smoke) must
  resolve the correct Tuolumne launch mechanism — likely `deepspeed --hostfile ... --launcher MPICH`
  under `flux run -N <nodes> -n <procs>`, or rewriting the launch as a direct `flux run python -m
  torch.distributed.run ...` equivalent (see software-pecan "DDP launch on Tuolumne (flux)" and
  feedback-torchrun-hpc-flags: `-n` is procs-per-node; e.g. 2 nodes x 4 GPUs = `-N 2 -n 4
  --gpus-per-proc 1`). Record whichever pattern works as a workload-megatron-deepspeed lesson.
  Dataset: script defaults to `/vc_data/...` placeholder paths — MUST be repointed to
  `<WS>/dataset/<run_name>/data` (a symlink into the Lustre `dataset_path` above) before first run;
  if no real pretraining corpus is readily available, use the app's included small-scale dummy/mock
  data generator if present (check `examples_deepspeed/rebase/README.md` and `tools/preprocess_data.py`)
  sized for a short smoke test — this is a decision point, confirm before spending time hunting for a
  full training corpus.
- **Build tool:** python (pip). `megatron_core` 0.2.0 editable install; DeepSpeed custom CUDA/HIP ops
  compile via `hipcc` at build/first-run time (JIT or precompiled — check DS_BUILD_OPS env var usage).
- **dftracer install env vars to use (from system_detect):**
  ```
  DFTRACER_BUILD_TYPE=RelWithDebInfo
  DFTRACER_ENABLE_MPI=ON
  MPICC=/opt/cray/pe/mpich/9.0.1/ofi/crayclang/20.0/bin/mpicc
  MPICXX=/opt/cray/pe/mpich/9.0.1/ofi/crayclang/20.0/bin/mpicxx
  DFTRACER_ENABLE_HDF5=ON
  HDF5_ROOT=/usr
  HDF5_DIR=/usr
  DFTRACER_DISABLE_HWLOC=OFF
  DFTRACER_CMAKE_ARGS="-DDFTRACER_ENABLE_TESTS=OFF -DDFTRACER_ENABLE_PYTHON=ON -DDFTRACER_ENABLE_MPI=ON \
    -DMPI_C_COMPILER=/opt/cray/pe/mpich/9.0.1/ofi/crayclang/20.0/bin/mpicc \
    -DMPI_CXX_COMPILER=/opt/cray/pe/mpich/9.0.1/ofi/crayclang/20.0/bin/mpicxx \
    -DDFTRACER_ENABLE_HDF5=ON -DHDF5_ROOT=/usr -DHDF5_PREFER_PARALLEL=ON -DDFTRACER_DISABLE_HWLOC=OFF"
  ```
  dftracer and the app MUST share the SAME venv (feedback-dftracer-aiml-venv) — never separate installs.
  mpi4py linkage: manylinux wheel + manual extract + patchelf `libmpi_cray.so` + `MPI4PY_MPIABI=mpich`
  (feedback-mpi4py-install) if mpi4py is a DeepSpeed/Megatron dependency; never `--no-binary` on NFS.
- **Tracing mode:** FUNCTION mode ONLY (source annotation via `DFTRACER_*_FUNCTION_START/END` or the
  Python `@dft_fn` decorator). NEVER `LD_PRELOAD=libdftracer_preload.so`.
- **dftracer_service node-counter daemon:** bracket every launch (smoke test, baseline trace, N-node
  validation) with `session_service_start` / `session_service_stop` — one instance per node, pinned to
  one core (`-n<nodes> -c1`, never `--tasks-per-node`).
- **Profiling:** already bound (`profile_bind` called by the router at the top of this planning session,
  MLflow parent run active). Every step below brackets its work with `profile_step_begin` /
  `profile_step_end` using the EXACT `## STEP N: <agent>` heading text as the `step` id.
- **Skills to consult per step:** `dftracer-ml-annotate`, `dftracer-annotate-python`,
  `software-pecan`, `software-molformer`, `workload-scaffold` (prior PyTorch DDP pipelines on this same
  system — reuse venv/module/torchrun-hpc-flags/mpi4py patterns). Create `software-megatron-deepspeed`
  for this app's own deltas (launcher resolution, ROCm wheel match, DS custom-op build) once discovered.
- **Run length policy:** ask the user for a time budget (target >=10 min training per run) before the
  baseline/optimization runs; calibrate epochs/iterations from a short probe on the baseline config, then
  FIX that iteration count across baseline and every optimizer variant. Take >=1 replicate of baseline and
  of the best variant to establish a noise band.

## STEP 1: dftracer-system-detect

Already executed by the router (see Overview for the full result: modules, LD_LIBRARY_PATH requirement,
MPI compiler paths, ROCm path, HDF5 detection). This step agent should re-confirm nothing has drifted
(`system_detect()` again) and record any NEW quirk (e.g. actual usable ROCm module version once resolved
in STEP 3/4) into `system-tuolumne` skill. No action needed if `system_detect()` output matches the Overview.

`profile_step_begin(step="STEP 1: dftracer-system-detect", agent="dftracer-system-detect")` ...
`profile_step_end(step="STEP 1: dftracer-system-detect", status="ok")`

## STEP 2: dftracer-session-setup

Session already exists and is `configured`: `run_id=megatron_deepspeed/20260726_185517`,
`app=https://github.com/argonne-lcf/Megatron-DeepSpeed`, ref=`main`. Two runs present: `baseline/source`
and `annotated/` (both 695 files, already cloned). This step is mostly a VERIFY pass:
1. `session_status(run_id)` — confirm subdirs (`performance, source, baseline, annotated, artifacts,
   scripts, tmp, build, install`) and `dataset_path=/p/lustre5/haridev/workspaces/megatron-deepspeed`.
2. `session_get_run_paths(run_id, run_name="baseline")` and `run_name="annotated"` — record `source_dir`,
   `traces_raw`, `traces_compact`, `scripts_dir` for downstream steps (paths are under the Overview's
   workspace root + `/baseline/...` or `/annotated/...`).
3. Confirm `<WS>/dataset` symlinks to the Lustre path above; if missing, create it now (before any run
   writes app data) — this is Pipeline Policy rule 11, mandatory.
4. Record `megatron_core==0.2.0` editable install requirement and Python 3.13.2 module in
   `workload-megatron-deepspeed` skill (create if absent).

Expected artifact: confirmation the workspace matches the Overview; a `<WS>/dataset` symlink to Lustre if
newly created; `run_paths` dict for `baseline` and `annotated` runs.

`profile_step_begin(step="STEP 2: dftracer-session-setup", agent="dftracer-session-setup")` ...
`profile_step_end(step="STEP 2: dftracer-session-setup", status="ok")`

## STEP 3: dftracer-build-app

Build the ORIGINAL (un-annotated) app in `baseline/source` to establish a working reference before any
instrumentation, using the module list + `CC`/`CXX` bound to cray-mpich from the Overview.

1. Load modules in the documented order (see Overview); do NOT reload StdEnv.
2. `export CC=/opt/cray/pe/mpich/9.0.1/ofi/crayclang/20.0/bin/mpicc`,
   `export CXX=/opt/cray/pe/mpich/9.0.1/ofi/crayclang/20.0/bin/mpicxx`.
3. Create/activate a Python 3.13.2 venv INSIDE the workspace (e.g. `<WS>/install/venv`) — this venv will
   be reused verbatim for the dftracer install (STEP 4) per feedback-dftracer-aiml-venv; never a separate
   install.
4. Install PyTorch ROCm wheel matching the loaded ROCm module (resolve the actual best-available ROCm
   module on Tuolumne — `/opt/rocm-4.2.0` is stated as old in system_detect; check `module avail rocm`
   for something newer that a ROCm PyTorch wheel targets) — verify with `python -c "import torch;
   print(torch.__version__, torch.version.hip, torch.cuda.is_available())"` before proceeding.
5. `pip install -e .` for `megatron_core` (editable, version 0.2.0) plus DeepSpeed
   (`pip install deepspeed` or repo-pinned version) — DeepSpeed will JIT/AOT compile custom ops via
   `hipcc`; capture the op-builder log, first compiler error if it fails.
6. mpi4py: follow feedback-mpi4py-install if mpi4py is required by Megatron/DeepSpeed's MPI backend
   (manylinux wheel + manual extract + patchelf `libmpi_cray.so` + `MPI4PY_MPIABI=mpich`).
7. Smoke-run a trivial import/CLI sanity check (e.g. `python -c "import megatron; import deepspeed"`)
   — this is NOT the training smoke test (that is STEP 5, on the ANNOTATED build); this step only proves
   the original app builds/installs on Tuolumne.
8. Save all build stdout/stderr to `<WS>/artifacts/03_build_app.log`.

Expected artifact: working venv at `<WS>/install/venv` with megatron_core + deepspeed installed;
build log in `artifacts/`; ROCm/PyTorch version pairing recorded.
DECISION POINT: if no compatible ROCm PyTorch wheel is found for `/opt/rocm-4.2.0`, surface this to the
user before picking a fallback (older wheel vs. newer ROCm module) — do not silently downgrade.

`profile_step_begin(step="STEP 3: dftracer-build-app", agent="dftracer-build-app")` ...
`profile_step_end(step="STEP 3: dftracer-build-app", status="ok")`

## STEP 4: dftracer-build-dftracer

Install dftracer INTO THE SAME VENV created in STEP 3 (mandatory — feedback-dftracer-aiml-venv).

1. Re-load the same modules; re-export the SAME `LD_LIBRARY_PATH` addition from the Overview
   (`/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:...:/usr/lib64:${LD_LIBRARY_PATH}`) BEFORE calling
   `session_install_dftracer` — it runs in a separate process that does not inherit prior Bash exports.
2. Use the exact env vars block from the Overview (`DFTRACER_BUILD_TYPE`, `DFTRACER_ENABLE_MPI=ON`,
   `MPICC`/`MPICXX` bound to cray-mpich, `DFTRACER_ENABLE_HDF5=ON`, `HDF5_ROOT=/usr`, `HDF5_DIR=/usr`,
   `DFTRACER_DISABLE_HWLOC=OFF`, full `DFTRACER_CMAKE_ARGS`).
3. Skip ROCProfiler / HIP tracing unless STEP 3 or annotation scoping (STEP 5) finds direct HIP usage in
   app source (system_detect says `hip_tracing_needed: false`) — per feedback-dftracer-install-rocm-mpi.
4. `pip install` dftracer with Python bindings enabled (`DFTRACER_ENABLE_PYTHON=ON` already in
   `DFTRACER_CMAKE_ARGS`). Verify: `python -c "import dftracer.dftracer"` succeeds in the SAME venv.
5. Verify MPI ABI compatibility between dftracer's linked MPI and the app's mpi4py (if used) — bind
   `CC`/`CXX` to the SAME `mpicc`/`mpicxx` as STEP 3, never a bare `which mpicc` (feedback-cc-cxx-mpi-selection).
6. Save install log to `<WS>/artifacts/04_install_dftracer.log`.

Expected artifact: dftracer importable in the shared venv; feature flags confirmed
(`DFTRACER_ENABLE_MPI=ON`, `HDF5=ON`, HIP tracing OFF); install log in `artifacts/`.

`profile_step_begin(step="STEP 4: dftracer-build-dftracer", agent="dftracer-build-dftracer")` ...
`profile_step_end(step="STEP 4: dftracer-build-dftracer", status="ok")`

## STEP 5: dftracer-annotator

Scope and annotate the `annotated/` copy (695 files present, mixed C/C++/Python). Load
`dftracer-ml-annotate` and `dftracer-annotate-python` skills before starting.

1. **Scoping (decision point — confirm before full annotation):** use a smoke-test command
   (short single-GPU/single-node forward+backward pass of `pretrain_gpt.py` with the ~345M config from
   the Overview, ~1-2 iterations) to identify which files are actually on the hot path. Do NOT blanket
   annotate all 695 files — Megatron-DeepSpeed vendors large unrelated subtrees (`examples/`,
   `examples_deepspeed/{azure,MoE,compression,...}`, `megatron/mpu` tests, etc.). Propose a file list to
   the user (expected scope: `megatron/training.py`, `megatron/model/*.py`, `megatron/data/*.py`,
   `pretrain_gpt.py`, DeepSpeed's own optimizer/engine step functions if editable/vendored, and any C/C++
   custom op `.cpp`/`.cu`/`.hip` sources under DeepSpeed if vendored into this repo) before annotating.
2. **Language split:** Python files -> `dftracer-annotate-python` (use `@dft_fn` decorator on the
   annotated hot-path functions: data loading, forward/backward/optimizer step, checkpoint save/load,
   collective ops wrappers). C/C++ files (if any DeepSpeed custom ops are vendored/build-from-source
   here, not from the installed pip wheel) -> `dftracer-annotator` with
   `DFTRACER_C_FUNCTION_START`/`END` macros.
3. **Exclude hot inner loops** that would be called millions of times (e.g. per-token/per-element
   kernels) — annotate at the function-call granularity discussed in `dftracer-ml-annotate`, not inside
   tight loops.
4. Watch for the known software-molformer pitfall: duplicate `dftracer.initialize_log()` calls in
   imported (non-entry-point) files silently corrupt traces — initialize the log ONCE, in the actual
   training entry point (`pretrain_gpt.py` / DeepSpeed launcher wrapper), nowhere else.
5. Validate annotations (lint pass: every START has a matching END, no double-init) before handing to
   STEP 6.
6. Grep-verify the annotator's own claims (per bug-annotator-fabricated-report lesson) — confirm the
   claimed file list was ACTUALLY modified on disk in `annotated/`, do not trust the summary alone.
7. Record file counts and scope decision into `pipeline_plan_changelog.md` and update this file's
   STEP 6 section with the confirmed final file list.

Expected artifact: annotated Python (and any in-repo C/C++) files under `<WS>/annotated/source/...`;
validated lint pass; confirmed scope list.

`profile_step_begin(step="STEP 5: dftracer-annotator", agent="dftracer-annotator")` ...
`profile_step_end(step="STEP 5: dftracer-annotator", status="ok")`

## STEP 6: dftracer-build-smoke

Build the ANNOTATED tree and run the smoke test — this is where the launcher mechanism (STEP 0's open
question) gets resolved concretely.

1. Reuse the SAME venv from STEP 3/4 (dftracer + app installed together); rebuild/reinstall
   `megatron_core` editable pointing at `<WS>/annotated/source` if the annotator worked on a separate
   copy, so imports pick up the instrumented files.
2. Resolve GPT config: uncomment "GPT-3 Medium 350M" block in
   `examples_deepspeed/rebase/ds_pretrain_gpt_125M.sh` per the Overview; repoint `DATA_PATH`/`BASE_PATH`
   at `<WS>/dataset/smoke/` (symlinked to the Lustre `dataset_path`).
3. Resolve the launcher: try `deepspeed --hostfile <flux-generated-hostfile> --launcher MPICH
   pretrain_gpt.py ...` invoked from inside a `flux run -N 1 -n <gpus-per-node> --gpus-per-proc 1`
   wrapper (per feedback-torchrun-hpc-flags and software-pecan's DDP-launch-on-Tuolumne pattern); if
   DeepSpeed's own launcher fights with flux's process placement, fall back to invoking
   `python -m torch.distributed.run` directly with DeepSpeed's engine API instead of the `deepspeed` CLI
   launcher. Record whichever works as a `software-megatron-deepspeed` lesson (create the skill).
4. Wrap dftracer_service (node-counter daemon) around the smoke launch:
   `session_service_start` before, `session_service_stop` after — one instance per node, 1 core each.
5. Set `DFTRACER_LOG_FILE` to `<WS>/annotated/traces/raw/smoke` (workspace, never Lustre) per
   feedback-optimization-pipeline-traces.
6. Run for a handful of iterations (enough to exit the DeepSpeed op-JIT/warmup phase and emit at least
   one full forward+backward+optimizer step). Verify: exit code 0 AND a non-empty `.pfw` trace file
   AND `python -c "import dftracer.dftracer"` succeeds in the run's env (not just build-time).
7. Save stdout/stderr to `<WS>/artifacts/06_build_smoke.log`.

Expected artifact: working annotated build; smoke test passes with non-empty `.pfw` trace(s) under
`<WS>/annotated/traces/raw/smoke`; resolved launcher command line to hand to STEP 7.
DECISION POINT: if DeepSpeed's custom-op JIT compile via hipcc fails or takes excessively long during
smoke test, surface to the user before spending the full baseline-run budget on it.

STATUS (2026-07-26, final attempt on alloc f3NaQ6bviHmH): two real blockers found and fixed this
attempt, but the run still did not complete a training iteration before the pdebug window closed:
1. FIXED -- megatron/training.py unconditionally imported
   megatron.model.vision.knn_monitor.compute_feature_bank, which imports vit_dataset.py ->
   torchvision; the custom ROCm torch build has no matching torchvision, and installing PyPI
   torchvision breaks (operator torchvision::nms does not exist). Fix applied: wrapped the import in
   try/except ImportError with a stub that raises only if actually called (call site at
   training.py:~1531 is already gated by args.vision_pretraining_type == "dino", never true for
   this GPT run). No other unconditional torchvision import exists outside vit_dataset.py.
2. FIXED -- after the vision-import fix, torch.cuda.set_device failed with
   "Error in dlopen: libcaffe2_nvrtc.so: cannot open shared object file" even though the .so is
   present under install/venv/.../torch/lib/: torch's lazy dlopen resolves by bare filename, not via
   its own RPATH, so torch/lib must be on LD_LIBRARY_PATH explicitly. Added to
   <WS>/scripts/smoke_test.sh:
   export LD_LIBRARY_PATH="$WS/install/venv/lib/python3.13/site-packages/torch/lib:${LD_LIBRARY_PATH}"
3. OBSERVED, NOT YET FIXED -- the fused-kernel JIT (megatron/fused_kernels/__init__.py) builds/links
   4 separate extensions (scaled_upper_triang_masked_softmax_cuda, scaled_masked_softmax_cuda,
   scaled_softmax_cuda, ...) sequentially into the SAME shared fused_kernels/build/ directory.
   The first run after a source change only builds/links whichever modules ninja considers stale
   that invocation, and the LAST module in the sequence intermittently fails with
   "ImportError: .../<name>.so: cannot open shared object file" even though the file exists on disk
   moments later -- looks like a race between ninja's own build-then-immediately-dlopen inside
   torch.utils.cpp_extension.load() when multiple extensions share one build dir. Workaround that
   worked: just retry the smoke script 2-3 times in a row (each retry relinks the already-compiled
   ones instantly via cache and only compiles the next stale one) until all 4 build in one pass.
   Root cause not fixed; consider giving each fused kernel extension its own build subdir, or
   pre-warming all 4 in one ninja invocation instead of 4 separate cpp_extension.load() calls.
4. NEW BLOCKING ISSUE (dataset scale) -- <WS>/scripts/smoke_test.sh points --data-path at
   <WS>/dataset/smoke/oscar-shuf-eod-gpt2bpe_text_document, which is a symlink to the FULL
   openwebtext/oscar corpus (288,714,672 documents). With --seq-length 64 this produces
   4,208,945,414 samples; Megatron's _build_index_mappings builds sample_idx.npy as an int64
   array of that many entries on a SINGLE rank before any iteration can start -- the file was still
   growing past ~67 GB in <WS>/dataset/smoke/index-cache/ after ~12 minutes of the ~19-min remaining
   pdebug window and did not finish; process was cancelled cleanly
   (flux proxy f3NaQ6bviHmH flux cancel <nested-jobid>) to avoid burning the rest of the allocation.
   Next attempt must NOT point smoke at the full corpus. Fix options for STEP 6 retry: (a) build a
   small synthetic/truncated .bin/.idx dataset (a few thousand documents) specifically for the
   smoke test, or (b) pass Megatron args that cap total samples (e.g. a small --train-samples
   combined with a pre-sliced document-index override) so _build_index_mappings operates on a bounded
   document count. The oversized index-cache/*.npy files were deleted after cancellation to reclaim
   space; regenerate with a properly small dataset next time.
   Trace files under <WS>/annotated/traces/raw/ from every attempt this session (including the
   final one, smoke-22e8252782d0a7c6-app.pfw.gz) are still 0 bytes -- no run has yet completed even
   one training iteration, so dftracer FUNCTION-mode flush has not been exercised end-to-end. This is
   the single remaining gate before STEP 7 can begin.
   Allocation f3NaQ6bviHmH (pdebug, 16 nodes, tuolumne[1033-1048]) was still live and otherwise
   healthy at the end of this attempt but not requested again per instruction; the NEXT build-smoke
   attempt should request a fresh allocation once the small-dataset fix above is applied.

   ATTEMPT 6 (2026-07-26, final attempt this session): CPU-side data-slice fix applied and
   verified. Built a truncated slice, `<WS>/dataset/smoke_slice/oscar-shuf-eod-gpt2bpe_text_document
   .{bin,idx}` (4,000 documents, ~7.8 MB), from the full corpus using
   `megatron.data.indexed_dataset.MMapIndexedDataset`/`MMapIndexedDatasetBuilder` directly (script at
   `<WS>/tmp/make_slice.py`, run via `<WS>/tmp/run_slice.sh` which sources `scripts/env.sh` +
   venv + sets `ROCM_HOME`/`ROCM_PATH` + `DFTRACER_ENABLE=0` -- importing `megatron.*` pulls in
   deepspeed's op_builder CUDA/ROCm compat check, which raises `MissingCUDAException` without
   ROCM_HOME set even for a pure CPU data-prep script). `scripts/smoke_test.sh`'s `DATA_DIR` now
   points at `$WS/dataset/smoke_slice` (vocab/merges/ds_config symlinked/copied alongside the slice).
   While building the slice, hit and fixed a REAL annotation bug: `megatron/data/indexed_dataset.py`
   `MMapIndexedDataset.Index.writer()` had decorator order `@_dft.log` above `@classmethod` -- this
   wraps the classmethod object itself (not the underlying function) and raised
   `TypeError: dft_fn.log.<locals>._decorator() takes 1 positional argument but 2 were given` on the
   very first call. Fixed by swapping order to `@classmethod` above `@_dft.log` (same class of bug as
   the existing Python Rule 3 `@property`-stacking pitfall in dftracer-annotate-python, but for
   `@classmethod`). This fix is a candidate addition to that skill/rule.
   BLOCKED at the finish line: by the time the slice + script fix were ready, the only live pdebug
   allocation (f3NaQ6bviHmH) had reached CLEANUP/timeout (~3600s/60min) before the GPU smoke test
   could be launched; the wrapper invocation itself also hit a `bash: -c: option requires an
   argument` shell-quoting issue in `gpu_smoke_wrapper.sh` when launched via `nohup ... &` that needs
   a quick look next time (works fine as a plain foreground `bash scripts/gpu_smoke_wrapper.sh
   <jobid>` per earlier attempts). Per instruction, no new allocation was requested. NEXT attempt:
   grab a fresh pdebug allocation, run `bash <WS>/scripts/gpu_smoke_wrapper.sh <jobid>` (foreground,
   not backgrounded/nohup) and confirm non-empty `.pfw` under `<WS>/annotated/traces/raw/`, then
   `session_capture_run_record(run_name="baseline_smoke")`.

STATUS: CLOSED/COMPLETE (2026-07-26, attempt 7, alloc f3Naz1zHGXUs, 2 nodes tuolumne[1022,1026]).
- Fixed `annotated/source/megatron/data/indexed_dataset.py`
  `MMapIndexedDataset.Index.writer()` decorator order: must be `@classmethod` above `@_dft.log`
  (was reversed -> TypeError on first call). Confirmed applied and working end-to-end.
- The GPT smoke config (2 layers, hidden=64, 4 heads, seq=64, micro/global-batch=1, train-iters=3,
  fp16, DeepSpeed ZeRO stage 0, gloo backend, 1 GPU) ran to completion via
  `bash <WS>/scripts/gpu_smoke_wrapper.sh <jobid>` (plain foreground, NOT nohup/background --
  confirms the earlier nohup quoting issue was purely an invocation artifact, not a script bug).
- First-run cost: the apex fused-kernel JIT (multi-arch build for gfx908/gfx90a/gfx942) took
  ~571s just for `scaled_upper_triang_masked_softmax_cuda` alone (cached across reruns in
  `<WS>/install/torch_ext_cache/`) -- budget >=15min wall time for a cold-cache smoke test.
  CAUTION: never manually `rm` files inside an in-progress `torch_ext_cache/<ext>/` dir (e.g. a
  stray lock file) -- this corrupts the ninja build state; if a rebuild is needed, remove the
  WHOLE extension's cache subdir (`find <ext_dir> -mindepth 1 -delete` -- `rm -rf` was blocked by
  the workspace's own permission policy) and let it rebuild clean from scratch.
- Verified via the produced trace itself (not just exit code): `<WS>/annotated/traces/raw/smoke-0b3f9de58ec6a7f7-app.pfw.gz`
  (191155 bytes, 19239 events, 138 unique instrumented function names incl. `train`, `pretrain`,
  `save_checkpoint`, `load_checkpoint`, `build_train_valid_test_data_loaders`,
  `GPTDataset.__len__`). The `train` span completed with `dur=18689us`; the outer `pretrain` span
  completed with `dur=587246945us` (~587s, dominated by the one-time apex JIT compile) -- both are
  `ph=1` (complete/duration) events with real non-zero durations, proving at least one full
  training iteration executed and the run exited normally (the "destroy_process_group() was not
  called" warning at end-of-log is Megatron-DeepSpeed's normal shutdown path, not a crash).
- `session_capture_run_record(run_name="baseline_smoke")` called; record dir
  `<WS>/baseline_smoke/record/`.
- STEP 7 (dftracer-tracer) may now proceed: reuse this exact venv/module/launcher/data-slice
  recipe, but scale the model back up to the "GPT-3 Medium 350M" config per the Overview and
  budget wall time generously for the (already-cached, so much cheaper) apex JIT warmup.

`profile_step_begin(step="STEP 6: dftracer-build-smoke", agent="dftracer-build-smoke")` ...
`profile_step_end(step="STEP 6: dftracer-build-smoke", status="ok")`

## STEP 7: dftracer-tracer

Baseline GPT (~345M) trace run at small multi-node scale using the resolved launcher from STEP 6.

1. ASK user for the time budget if not already answered this session (target >=10 min training,
   per DL run-length policy) — then calibrate: run a short probe on baseline config, measure
   seconds/iteration, fix `iterations = floor(budget_seconds / seconds_per_iteration)` for THIS run and
   every later optimizer variant (STEP 9). Fix problem_scale/global_batch_size/checkpoint_interval too
   unless one of them is the knob under test.
2. Use the EXISTING allocation (job id `f3NYii2RimCB`): verify remaining time
   (`flux jobs -no "{id} {state} {t_remaining}" f3NYii2RimCB`), then
   `flux proxy f3NYii2RimCB bash <wrapper>.sh ...` with `run_in_background: true` — 1-2 nodes of the 16
   for this baseline (do not consume the full allocation).
3. Write a bash wrapper script (never inline module loads) under `<WS>/scripts/baseline_trace.sh` that:
   loads modules, activates the shared venv, sets `CC`/`CXX`, sets `LD_LIBRARY_PATH`,
   sets `DFTRACER_LOG_FILE=<WS>/baseline/traces/raw/baseline` (or the `annotated` run's trace dir —
   confirm which run_name this baseline attaches to; use `session_get_run_paths(run_id,
   run_name="baseline")` output from the Overview: `traces_raw=.../baseline/traces/raw`), points app
   data dirs at `<WS>/dataset/baseline/` (Lustre symlink), runs the resolved launcher command from
   STEP 6 at the fixed iteration count, brackets the launch with `session_service_start`/
   `session_service_stop` (dftracer_service, 1 instance/node, 1 core each), and calls
   `session_run_with_dftracer`.
4. Cwd for ALL of this MUST be inside `<WS>/annotated/source` (or wherever the built annotated tree
   lives) — never the project root (feedback-app-exec-cwd).
5. After the run: `session_split_traces` on the produced `.pfw` files into
   `<WS>/baseline/traces/compact` — record file count and output directory.
6. Take at least one replicate to establish a noise band (per DL run-length policy), if time budget
   allows within this stage; otherwise flag as pending and let the analyzer/optimizer proceed on the
   first replicate while the second collects in parallel (eager pipelining — do not block STEP 8 on it).

Expected artifact: `run_name="baseline"` trace files under `<WS>/baseline/traces/raw` and split output
under `<WS>/baseline/traces/compact`; wrapper script at `<WS>/scripts/baseline_trace.sh`; measured
seconds/iteration and the fixed iteration count for all later runs.

`profile_step_begin(step="STEP 7: dftracer-tracer", agent="dftracer-tracer")` ...
`profile_step_end(step="STEP 7: dftracer-tracer", status="ok")`

## STEP 8: dftracer-analyzer / dftracer-diagnoser

Analyze the baseline trace, then diagnose bottlenecks. This app is a PyTorch DDP training loop
(compute + NCCL/RCCL-style collective communication dominated, like software-pecan/software-molformer/
workload-scaffold), NOT an HDF5-heavy I/O workload — expect analysis to show compute/communication as
dominant and I/O as comparatively low, but still run every view.

1. `dftracer-analyzer`: preset — use the generic/DL preset (not the HDF5-only preset) since this app is
   PyTorch-training-shaped; run with `cluster_n_workers=32` (never `cluster_cores`, per
   feedback-analysis-parallel-workers) so analysis finishes in minutes on `<WS>/baseline/traces/compact`.
   Produce views for: time breakdown by phase (data load / forward / backward / optimizer step /
   checkpoint), per-rank load imbalance, and collective-communication time share.
2. `dftracer-diagnoser`: consume the analyzer output and produce a ranked bottleneck list. If checkpoint
   I/O appears, run `diagnose_checkpoint` explicitly (that wiring was previously broken and fixed — see
   feedback-dftracer-analyzer-generic-preset — confirm it now runs cleanly).
3. Save analysis/diagnosis artifacts (stdout, any generated report) to
   `<WS>/artifacts/08_analyze_diagnose.log` and a structured bottleneck list for STEP 9.
4. Eager pipelining: if a second baseline replicate (from STEP 7 note) becomes available while this step
   runs, feed it in to corroborate the noise band rather than waiting for it before starting.

Expected artifact: ranked bottleneck list (e.g. "communication-bound: N% time in NCCL/RCCL all-reduce",
"compute-bound: N% forward/backward", "I/O negligible: N%") to hand to the optimizer.

`profile_step_begin(step="STEP 8: dftracer-analyzer+diagnoser", agent="dftracer-analyzer")` ...
(then re-open for diagnoser sub-portion if tracked separately, or keep as one bracket)
`profile_step_end(step="STEP 8: dftracer-analyzer+diagnoser", status="ok")`

## STEP 9: dftracer-optimizer

Run ALL FOUR optimizer dimensions exhaustively against the STEP 8 bottleneck list — mandatory even if
one dimension dominates (Pipeline Policy rule 14):

1. `dftracer-optimizer-io` — walk the full `dftracer-io-optimization` checklist. Given this is a PyTorch
   DDP training loop with HDF5 status "present but likely not on the hot path" (system_detect: HDF5
   1.10.5, but app is Megatron indexed-dataset binary format, not HDF5), expect most L1/L2/L3 candidates
   to be "not applicable — dataset format is memory-mapped binary indexed dataset, no HDF5 in the
   training hot path" — but document EVERY checklist item with an explicit verdict, not a skip.
2. `dftracer-optimizer-communication` — likely dominant dimension for DDP: gradient all-reduce bucketing,
   ZeRO stage tuning (the script defaults `ZERO_STAGE=2` in `run_deepspeed_example.sh`/rebase scripts —
   try ZeRO-1 vs ZeRO-2 vs ZeRO-3 tradeoffs), overlap of comm with backward compute, NCCL/RCCL env
   tuning for the Cray Slingshot fabric (libfabric/match_SHS already loaded).
3. `dftracer-optimizer-compute` — mixed precision (fp16/bf16 — script uses fp16 with
   `initial_scale_power`), activation checkpointing (`--deepspeed-activation-checkpointing` already used
   in the example — check if it helps or hurts at this small scale), kernel fusion options.
4. `dftracer-optimizer-memory` — ZeRO stage vs. memory tradeoff, activation checkpointing memory savings,
   micro-batch size vs. global-batch-size tuning given MI300A's unified CPU/GPU memory.
5. For each proposed variant: fix the SAME iteration count/global_batch_size as STEP 7's baseline unless
   the knob under test IS one of those (state clearly if so — changes total work, must be called out).
6. Compare each variant's wall-clock/throughput against the STEP 7 baseline + noise band; validate the
   BEST variant at the same or slightly larger scale (still within the existing `f3NYii2RimCB`
   allocation, verify remaining time before launching).
7. Watch feedback-shared-source-tree-race: do not run two optimizer variants in parallel against the SAME
   shared `annotated/source` tree if they edit overlapping files — serialize or use separate copies.
8. Save each variant's script under `<WS>/scripts/optimizer_<dimension>_<variant>.sh` and its log under
   `<WS>/artifacts/09_optimizer_<dimension>_<variant>.log`.

Expected artifact: merged 4-dimension proposal report (every checklist item, applicable or not, with a
documented verdict) + measured delta for the best applied variant vs. baseline, validated at scale.

`profile_step_begin(step="STEP 9: dftracer-optimizer", agent="dftracer-optimizer")` ...
`profile_step_end(step="STEP 9: dftracer-optimizer", status="ok")`

## STEP 10: dftracer-report

Assemble `final_report/` — MANDATORY, every session, regardless of how much of the pipeline completed.

1. Call `session_final_report` with a fully detailed `report_md` (system, app, workload config used —
   ~345M GPT via the "GPT-3 Medium 350M" block, baseline measurement, all 4 optimizer dimensions'
   checklist verdicts, the best variant's validated delta + noise band), `conversation_md` (narrative of
   what was tried, including the launcher-resolution decision from STEP 6), and `readme_md` that lets a
   reader with ONLY `final_report/` reproduce the session: every script named under `scripts/`,
   `config.ini`/`WORKSPACE_ROOT` explained, a literal runnable command, a pointer back to `REPORT.md`.
2. `config.ini` is the ONLY place a real path/session value goes; grep `scripts/` for the workspace's
   absolute path to confirm no script hardcodes it.
3. Self-contained validation: point `OUTPUT_ROOT` at `<WS>/final_folder_validate/` (isolated from the
   session's own run data), fill `WORKSPACE_ROOT`, run `scripts/run_all.sh <alloc-id>` using only
   `final_report/` contents; re-call `session_final_report` with `validated=True` and a one-line
   `validation_notes` once it reproduces within noise.
4. Verify the three gates before marking this step done: `pdf.generated`, `completeness.ok`,
   `readme_check.ok` — all true. If any is false, fix the named gap and re-call, do not mark done on a
   partial pass.

`profile_step_begin(step="STEP 10: dftracer-report", agent="dftracer-report")` ...
`profile_step_end(step="STEP 10: dftracer-report", status="ok")`

## STEP 11: dftracer-privacy-guard

Final step, MANDATORY, after all self-learning writes.

1. `privacy_scan(paths=[..., "final_report"])` — `final_report/` is NOT in the tool's default scan set,
   include it explicitly (per bug-privacy-scan-final-report-gitignore-blindspot).
2. Redact any usernames, absolute user paths (`/usr/WS2/<user>/...`, `/p/lustre5/<user>/...`), flux job
   ids (`f3NYii2RimCB`), session UUIDs found in persisted skill/lesson/memory writes from STEPs 1-10.
3. Re-scan until `clean`.

`profile_step_begin(step="STEP 11: dftracer-privacy-guard", agent="dftracer-privacy-guard")` ...
`profile_step_end(step="STEP 11: dftracer-privacy-guard", status="ok")`
then `profile_report()` a few seconds later.

## DISPATCH ORDER

dftracer-system-detect, dftracer-session-setup, dftracer-build-app, dftracer-build-dftracer,
dftracer-annotator, dftracer-build-smoke, dftracer-tracer, dftracer-analyzer, dftracer-diagnoser,
dftracer-optimizer, dftracer-report, dftracer-privacy-guard
