---
name: project-megatron-deepspeed-bert-optimization
description: Megatron-DeepSpeed BERT-Base dftracer pipeline on Tuolumne (4N/16R MI300A) — pipeline complete, +47.8% train_step speedup validated via multi-replicate A/B, final_report assembled and privacy-clean
metadata:
  type: project
---


**Canonical home:** see the new `software-megatron-deepspeed` skill (this file's full
content is now persisted there). The two sibling write-ups this file references
(`software-megatron-deepspeed-compute-tuning-findings`, `project-megatron-deepspeed-gpt-pipeline`)
do not exist anywhere in project memory or skills — flagged as dangling, not fabricated.

Session `megatron_deepspeed/20260727_164229` (Megatron-DeepSpeed **BERT-Base**, 110M
params) — full pipeline complete: annotation (11 files / 231 dftracer decorators),
multi-replicate A/B baselines, root-cause diagnosis, 4-dimension optimization sweep
(I/O, communication, compute, memory all walked), final_report assembled and
privacy-clean.

**Headline: measured 47.8% train_step speedup** (400 iters, 16 ranks / 4 nodes),
replicate-confirmed, from two config-only changes:
1. **RCCL transport fix (-43.1%)**: RCCL was silently falling back to host TCP
   sockets because the site's `aws-ofi-rccl` plugin was never on `LD_LIBRARY_PATH`.
   Fix: add the plugin lib dir to `LD_LIBRARY_PATH`, leave `NCCL_NET` UNSET (do NOT
   set `NCCL_NET=libfabric` — the plugin registers as `AWS Libfabric`, and the wrong
   name forces a hard failure that was originally misread as an ABI mismatch).
2. **`--num-workers 2` (-8.2%)**: BERT's inline MLM masking
   (`create_masked_lm_predictions`) is real per-batch CPU work on the critical path
   at `--num-workers 0`.

Wall time is NOT the metric of record for this workload (dominated by ~40-48s of
allocation/init-time startup variance) — `train_step` summed over 400 iterations is.
Noise band: ~4.9% on socket transport (n=3), ~1.9% on libfabric (n=4).

**Corrected findings this session (each cost real time to root-cause):**
- "Plugin is ABI-incompatible" — WRONG. It's a naming mismatch (`NCCL_NET=libfabric`
  never matches the plugin's `AWS Libfabric` registration name), not an ABI issue.
- "gloo vs nccl gave only +1.2%, so transport doesn't matter" — SUPERSEDED. Both
  backends were on host TCP; the comparison was null by construction.
- "BERT has 9x GPT's I/O (7.3% POSIX @ 17.7 MB/s)" — RETRACTED. One-off transient
  Lustre stall (140.42s stat calls); re-measured standalone at 0.19ms. Contaminated
  baseline was voided, not optimized against.
- Percentages were originally computed against dfanalyzer's rank-summed App total
  (1933.3s), not per-rank wall (~198s) — understated shares ~16x. All corrected.
- `rocm/6.4.3leakfix` "-4.9%" — NOT SUPPORTED as a performance claim (A/B gave -2.2%
  with overlapping ranges); kept for its real memory-leak fix, not performance.

**Negative/null results recorded:** bf16 -25% (carried over from sibling GPT
session), ZeRO-1 -20%/+20% regression (confirmed on both transports — not a
transport artifact; explained by capacity arithmetic, ZeRO-1 state is only 1.4% of
HBM so there's no pressure to relieve), `TORCH_BLAS_PREFER_HIPBLASLT` inert, all four
`--use-flash-attn*` flags silently no-op (dependency absent), kernel fusion already
on by default, NUMA/affinity a measured no-op on the MI300A APU (unified CPU/GPU
memory).

**Honest gaps:** FlashAttention untested (needs a multi-hour ROCm/CK build) — highest
remaining ceiling. Checkpoint I/O never exercised. Memory-bandwidth roofline
unresolved (no HBM counter available). `pipeline_plan.md` was never created for this
session (process gap — stages dispatched directly). Two single-replicate probes
(`--log-interval 10`, ZeRO-1-on-libfabric) should get a second replicate.

**final_report/ mechanics note (useful for future sessions):** the tool's automated
script-glob missed 20+ scripts this session (env.sh/env_ofi.sh/env_baserocm.sh,
ab_driver*.sh, lever_driver.sh, all run_ab_*/run_ofi_*/run_L_*.sh, ds_config_bert_*.json,
ab_summarize.py) — these had to be manually copied into `final_report/scripts/` and
anonymized (literal ALLOC_ID -> `${1:?...}`, literal `/p/lustre5/<user>/...` dataset
path -> `${DATASET_ROOT}` sourced from a new `config.ini` entry). **Also confirmed:
re-calling `session_final_report` regenerates `config.ini` from scratch each time**,
dropping any manually-appended config vars (like `DATASET_ROOT`) — re-append after
every re-call before the final privacy scan. Also confirmed the tool's `report_md`/
`conversation_md`/`readme_md` params behave as: `overwrite=False` + blank string
preserves existing content, but `overwrite=False` + non-blank text OR `overwrite=True`
always WRITES what you pass (there is no "keep existing" placeholder string — passing
literal text like "KEEP" or "PLACEHOLDER" overwrites the file with that literal text).
Always resupply the FULL content in the same call as any other section you're fixing.

privacy_scan/privacy_redact ran clean at the end (38 pre-existing findings in
`good-runs/` example snapshots were also redacted as part of this pass — unrelated
files that happened to be in the scanned tree, not part of this session's own
workspace, but flagged and fixed since privacy_scan operates project-wide).

See also: [[software-megatron-deepspeed-compute-tuning-findings]],
[[project-megatron-deepspeed-gpt-pipeline]] (sibling GPT-350M session, same system).
