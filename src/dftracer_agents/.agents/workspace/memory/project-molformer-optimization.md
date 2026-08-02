---
name: project-molformer-optimization
description: MoLFormer (chemical-language transformer) annotate/optimize/validate pipeline on Tuolumne — complete, +206% validated at batch=128, final_report assembled and privacy-clean
metadata:
  type: project
---

**Canonical home:** see the `software-molformer` skill (full build/annotation/optimization
lesson set), `system-tuolumne` (ROCm/PyTorch version-match + `lightning_fabric` lessons),
and `dftracer-annotation-lessons` (PP9, duplicate `initialize_log()`).

Completed a full dftracer pipeline session for IBM MoLFormer (PyTorch + PyTorch Lightning +
apex, github.com/IBM/molformer) on Tuolumne (AMD MI300A APU, ROCm 6.3.1). Session:
`<session>` (was `molformer/20260723_235016`).

**Outcome:** SUCCESS end to end — annotate -> baseline trace -> analyze -> diagnose ->
4-way optimizer dispatch -> N-node validation -> final_report, all completed. 15 distinct
real environment/app bugs fixed to reach a working GPU smoke test and baseline trace.

**Key technical findings:**
- Root cause of a major "empty trace" mystery: two imported (non-entry-point) library files
  (`pubchem_encoder.py`, `dataset_pubchem.py`) carried erroneous duplicate
  `dftracer.initialize_log()` calls that corrupted the C++ profiler singleton before the real
  entry point's own init ran. Fix: init/finalize calls belong ONLY in true entry points. This
  is a generically-applicable dftracer annotation pitfall, recorded as PP9 in
  `dftracer-annotation-lessons` skill.
- ROCm/PyTorch wheel version must exactly match the loaded `rocm/X.Y.Z` module.
  `libcaffe2_nvrtc.so` needs an explicit `LD_LIBRARY_PATH` entry to
  `<venv>/torch/lib`. `lightning_fabric`'s Ampere-capability check crashes on AMD GPU device
  names and needed a targeted patch/skip. apex must be built from the `ROCm/apex` fork
  (upstream NVIDIA/apex hard-requires nvcc/CUDA_HOME, absent on this cluster).
- STEP 9 validation (1 node x 4 MI300A GPUs, real 20K-molecule PubChem slice, DDP
  world_size=4, fixed 1-epoch work): baseline 536 samples/s -> batch=128/GPU + workers=2 =
  1638 samples/s (+206%, best config). bf16-mixed precision measured WORSE (-43%, rejected)
  at this ~1M-param model scale — GradScaler overhead dominates below the compute-bound
  threshold. `expandable_segments` allocator flag confirmed NOT SUPPORTED on ROCm (explicit
  PyTorch warning, silent no-op). num_workers 1 vs 2 was neutral/noise at this dataset scale.
  Batch size is the real lever, not precision or allocator tricks.

**Skills updated (persisted, user-approved per already-signed-off technical decisions):**
- `software-molformer` (new) — full build/annotation/optimization lesson set for this app.
- `system-tuolumne` — added ROCm/PyTorch version-match + `lightning_fabric` Ampere-check
  lessons under "Deep Learning / PyTorch (ROCm) Workloads".
- `dftracer-annotation-lessons` — added PP9 (duplicate initialize_log in imported files).

**Final report:** `<session>/final_report/` — self-contained, config.ini + lib_load_config.sh,
all 7 run scripts (baseline + 5 STEP 9 validation variants + run_all.sh) copied in and
anonymized (grepped clean of the workspace's absolute path). Validated structurally (config
gating, path resolution, `bash -n` syntax on every script) rather than by re-launching a new
GPU job during report finalization — full GPU-scale re-execution of the throughput numbers
was not repeated this pass. privacy_scan run on both the git-tracked skill trees and
`final_report/` before considering the session done.

**Known gap:** `dftracer_service` node-counter daemon could not be composed with the
flux-wrapped compute-node launch path used for STEP 9 — no node-level counters captured for
the validation run, only per-rank app traces. Worth a future MCP-tool fix if multi-node
node-counter coverage becomes a hard requirement for DL workloads launched this way.
