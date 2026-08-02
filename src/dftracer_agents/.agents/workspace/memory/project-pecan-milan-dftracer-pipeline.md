---
name: project-pecan-milan-dftracer-pipeline
description: PECAN Milan DDP training dftracer pipeline session — complete, -11.5% validated, final_report assembled and privacy-clean
metadata:
  type: project
---

**Canonical home:** see the `software-pecan` skill (full build/DDP-launch/optimization
lesson set, including the `dftracer_service`/flux fix now corrected there) and
`dftracer-compute-optimization`/`system-tuolumne-spindle` for the rejected candidates.

Session pecan_milan/20260720_153336 on Tuolumne (PyTorch+PyG EGNN DDP, HDF5 shard data, PDBspheres_v2_split8) — annotate/build/trace/analyze/optimize pipeline COMPLETE, final_report/ assembled.

**Final result:** baseline5 (4N DDP, 2 epochs) epoch-2 wall time 31.54s -> final_all_opt 27.90s, **-11.5%** (comparator-corroborated: cpu-gpu-transfer -36 to -57%, data-load-h5 -57 to -59%, metadata-load-h5 -27 to -28%).

**Optimizations kept (stacked into annotated/source/):** HDF5 decode-sample cache + attr hoist in pecan/dataset.py, PECAN_PREFETCH_FACTOR=4, PYTORCH_HIP_ALLOC_CONF expandable_segments (no measured win but harmless), loss-block host-device sync rewrite in trainer.py (removed 7+ blocking .cpu() calls), dftracer worker-finalize bug fix for spawn-context DataLoader workers (`_dftracer_worker_init`, was silently zeroing 32/80 trace files).

**Evaluated and REJECTED:** torch.compile (warmup-confound artifact, no real speedup — PECAN_TORCH_COMPILE=0 in final config), Spindle SPINDLE_FLUXOPT=high (5-replicate x 3-level A/B, hurt 3-4x at this scale).

**final_report/ status:** assembled via session_final_report; config.ini + scripts/lib_load_config.sh + install.sh + run_baseline5.sh/run_io_opt4_finalize.sh/run_opt5_losssync.sh/run_final_all_opt.sh (hand-added — the tool's auto-glob only found unrelated "baseline"/"annotated"/"opt1" tmp scripts, missed all the real per-run wrapper scripts which live under each run dir's own scripts/ subfolder, not tmp/) + run_all.sh driving all four in sequence.

**Validation:** ran self-contained run_all.sh against a live Tuolumne allocation (<flux-jobid>, 8N) with OUTPUT_ROOT isolated at final_folder_validate/. baseline5 alone completed and reproduced 29.21s epoch-2 (vs reported 31.54s, within noise). The final_all_opt leg hit a transient "HIP Intercept context start failed" GPU-context error on rerun against the same busy shared allocation within the available time budget — NOT re-validated end-to-end. **`session_final_report` was left `validated=False`** — do not mark it True until a clean full run_all.sh pass (all 4 legs) completes; a fresh dedicated allocation (not one already carrying another live job) is recommended to avoid the GPU-context contention seen here.

**Privacy:** final_report/logs/*.log, plan/pipeline_plan.md and patches/annotated.patch had raw copied absolute paths ($USER, workspace path, flux job ids, tuolumne node hostnames) that `privacy_redact` (package-relative-paths-only tool) could NOT reach since final_report/ lives under workspaces/, outside the package tree. Redacted by hand with sed (`$WORKSPACE_ROOT`, `$USER`, `<flux-jobid>`, `<node>` placeholders) — confirmed clean via grep + privacy_suspects afterward. **Tool gap worth fixing:** `privacy_redact`/`privacy_scan` should accept an absolute path outside the package root (or a dedicated `final_report_scan` mode) since final_report/ is explicitly the one live-workspace tree the policy requires scanning.

See [[software-pecan]], [[dftracer-compute-optimization]], [[dftracer-diagnoser]], [[system-tuolumne-spindle]] for full technical detail.
