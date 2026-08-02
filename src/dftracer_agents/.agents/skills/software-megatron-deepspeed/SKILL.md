---
name: software-megatron-deepspeed
description: >
  Megatron-DeepSpeed BERT-Base dftracer pipeline findings on Tuolumne (AMD MI300A,
  4 nodes / 16 ranks) — RCCL transport fix, dataloader worker fix, corrected
  measurement methodology, and confirmed negative/null results. Megatron-DeepSpeed
  is annotated/instrumented by dftracer (software-* naming, not workload-*, per
  the software-vs-workload naming convention). Load this skill for any
  Megatron-DeepSpeed session.
---

Cross-references: [[software-rocm]] [[software-mpi]] [[dftracer-optimization-kb]] [[system-tuolumne]]

---

## Session: BERT-Base, 4 nodes x 4 GPUs = 16 ranks, MI300A

Full pipeline complete: annotation (11 files / 231 dftracer decorators),
multi-replicate A/B baselines, root-cause diagnosis, 4-dimension optimization sweep
(I/O, communication, compute, memory all walked), final_report assembled and
privacy-clean.

**Headline: measured 47.8% train_step speedup** (400 iterations, 16 ranks / 4 nodes),
replicate-confirmed, from two config-only changes:

1. **RCCL transport fix (-43.1%).** RCCL was silently falling back to host TCP
   sockets because the site's `aws-ofi-rccl` plugin was never on
   `LD_LIBRARY_PATH`. Fix: add the plugin's lib dir to `LD_LIBRARY_PATH`, and
   leave `NCCL_NET` UNSET — do NOT set `NCCL_NET=libfabric`, since the plugin
   registers as `AWS Libfabric` and the name mismatch forces a hard failure that
   is easy to misread as an ABI incompatibility rather than a naming mismatch.
   See `software-rocm`'s "RCCL transport selection" section for the generic
   version of this lesson.
2. **`--num-workers 2` (-8.2%).** BERT's inline MLM masking
   (`create_masked_lm_predictions`) is real per-batch CPU work sitting on the
   critical path at `--num-workers 0`.

`train_step` summed over 400 iterations is the metric of record — wall time is
NOT, since it is dominated by ~40-48s of allocation/init-time startup variance
that swamps the per-step signal. Noise band: ~4.9% on the socket-transport
config (n=3 replicates), ~1.9% on the libfabric-fixed config (n=4 replicates).

## Corrected findings (each cost real time to root-cause — read before re-deriving)

- **"Plugin is ABI-incompatible" — WRONG.** It's a naming mismatch
  (`NCCL_NET=libfabric` never matches the plugin's `AWS Libfabric` registration
  name), not an ABI issue.
- **"gloo vs nccl gave only +1.2%, so transport doesn't matter" — SUPERSEDED.**
  Both backends were actually on host TCP at the time of that comparison; the
  comparison was null by construction, not evidence that transport is
  irrelevant.
- **"BERT has 9x GPT's I/O (7.3% POSIX @ 17.7 MB/s)" — RETRACTED.** One-off
  transient Lustre stall (140.42s of `stat` calls); re-measured standalone at
  0.19ms. The contaminated baseline was voided, not optimized against.
- Percentages were originally computed against dfanalyzer's rank-summed App
  total (1933.3s), not per-rank wall time (~198s) — this understated shares by
  ~16x. All figures in this skill are corrected.
- `rocm/6.4.3leakfix` "-4.9%" is **NOT SUPPORTED as a performance claim** (a
  clean A/B gave -2.2% with overlapping ranges) — keep it for its real
  memory-leak fix, not for a performance win.

## Negative/null results (recorded so future sessions don't re-test from scratch)

bf16 -25% (carried over from a sibling GPT-350M session on the same system);
ZeRO-1 -20%/+20% regression (confirmed on BOTH transports — not a transport
artifact; ZeRO-1's optimizer-state savings are only 1.4% of HBM on this model
size, so there is no memory pressure to relieve); `TORCH_BLAS_PREFER_HIPBLASLT`
inert; all four `--use-flash-attn*` flags silently no-op (dependency absent —
FlashAttention was never actually exercised, see "Honest gaps" below); kernel
fusion already on by default; NUMA/affinity a measured no-op on the MI300A APU
(unified CPU/GPU memory — reconfirms the same finding seen on IOR/h5bench/
ScaFFold sessions on this system).

## Honest gaps (do not silently forget these when resuming)

- FlashAttention untested — needs a multi-hour ROCm/CK build; this is the
  highest remaining optimization ceiling for this workload.
- Checkpoint I/O never exercised this session.
- Memory-bandwidth roofline unresolved — no HBM counter available on this
  system.
- `pipeline_plan.md` was never created for this session (a process gap —
  stages were dispatched directly instead of through a written plan).
- Two single-replicate probes (`--log-interval 10`, ZeRO-1-on-libfabric) should
  get a second replicate before being treated as settled.

## `final_report/` mechanics (useful for future sessions, not Megatron-specific)

- The tool's automated script-glob missed 20+ scripts this session
  (`env.sh`/`env_ofi.sh`/`env_baserocm.sh`, `ab_driver*.sh`, `lever_driver.sh`,
  all `run_ab_*`/`run_ofi_*`/`run_L_*.sh`, `ds_config_bert_*.json`,
  `ab_summarize.py`) — these had to be manually copied into
  `final_report/scripts/` and anonymized (literal alloc id ->
  `${1:?...}`, literal Lustre dataset path -> `${DATASET_ROOT}` sourced from a
  new `config.ini` entry).
- **Re-calling `session_final_report` regenerates `config.ini` from scratch
  every time**, dropping any manually-appended config vars (like
  `DATASET_ROOT`) — re-append after every re-call, before the final privacy
  scan.
- Confirmed the tool's `report_md`/`conversation_md`/`readme_md` params behave
  as: `overwrite=False` + a blank string preserves existing content, but
  `overwrite=False` + non-blank text OR `overwrite=True` always WRITES what you
  pass — there is no "keep existing" placeholder string (passing literal text
  like `"KEEP"` or `"PLACEHOLDER"` overwrites the file with that literal text).
  Always resupply the FULL content in the same call as any other section
  you're fixing.
- `privacy_scan`/`privacy_redact` ran clean at the end of this session (some
  pre-existing findings in unrelated `good-runs/` example snapshots were also
  redacted as part of the same pass, since `privacy_scan` operates
  project-wide, not just over this session's own workspace).

## Known dangling references (not yet recovered)

This skill's source memory entry referenced two sibling write-ups that do not
currently exist anywhere in project memory or skills:
`software-megatron-deepspeed-compute-tuning-findings` (presumably a deeper
compute-optimization write-up) and `project-megatron-deepspeed-gpt-pipeline`
(a sibling GPT-350M session on the same system, referenced above for the bf16
and transport findings). If a future session locates that content, fold it in
here; do not fabricate it in the meantime.
