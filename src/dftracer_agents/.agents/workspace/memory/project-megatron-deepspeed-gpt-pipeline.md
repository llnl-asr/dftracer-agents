---
name: project-megatron-deepspeed-gpt-pipeline
description: Megatron-DeepSpeed GPT-350M dftracer pipeline on Tuolumne — STEPS 1-10 complete (final_report assembled, privacy-clean); BERT deferred/never started; rule-12 service-daemon gap noted
metadata:
  type: project
---

## Status: STEPs 1-10 complete for `megatron_deepspeed/20260726_185517`

GPT-3 Medium 350M on Tuolumne (MI300A/ROCm/Cray/Flux). BERT was in-scope in the
original plan but was **deferred and never started** this session — scope every
future reference to this session accordingly.

### Pipeline outcome
- Baseline: 4N x 4-rank, gloo, ZeRO-0, fp16 — 145.4 samples/sec steady-state
  (iters 2-3; iter1 = apex-JIT warmup, excluded everywhere). Took ~10 attempts
  to get a genuine 16-rank distributed run; every failure was a distinct real
  bug (see [[software-megatron-deepspeed-launcher-world-size-1]] and related).
- Diagnosis: compute-bound (67% training step), init/setup 29%, I/O negligible
  (0.8%, zero POSIX ops in steady-state training).
- All 4 optimizer dimensions dispatched per policy rule 14. Every measured
  lever was neutral-or-regression: nccl/RCCL backend +1.2% (noise, a
  trace-derived 35% estimate was ~30x too high), DeepSpeed ZeRO-1 -20.2%,
  bf16 -25% (2 independent measurements agree), TORCH_BLAS_PREFER_HIPBLASLT
  inert both directions (-2.0%/-0.6%). See
  [[software-megatron-deepspeed-compute-tuning-findings]] for the compute-side
  detail. **Headline: baseline is already near-optimal for this scale/stack.**
- FlashAttention remains the highest-ceiling untested lever (~17% est.),
  blocked on a multi-hour ROCm/CK build not attempted this session.

### STEP 10 (final_report) — complete
- `final_report/` assembled at `<WS>/final_report/` (WS = the session
  workspace): REPORT.md (all 11 mandated sections + ledger + N-way comparator
  table), REPORT.pdf auto-rendered, README.md with full script inventory
  (core repro path in `scripts/`, optimization variants collected under
  `scripts/opt/` — the tool's default glob only picked up `tmp/*.sh` diagnostic
  wrappers, NOT the top-level `scripts/run_opt_*.sh` / `optimizer_*.sh` /
  `ds_config_*.json` files, so these had to be hand-copied into
  `final_report/scripts/opt/` and `final_report/patches/opt_configs/` and
  re-pathed through `lib_load_config.sh` — see the tool-gap note below).
- `completeness.ok=true`, `readme_check.ok=true`, `pdf.generated=true`.
- **NOT validated=True** — no fresh allocation was available to run
  `scripts/run_all.sh` end-to-end in an isolated scratch dir this session.
- Privacy: `final_report/` initially had 19 files with real leaks (MLflow
  parent_run_id, flux job ids embedded in per-step JSON/log excerpts,
  `tuolumneNNNN` hostnames in diagnostic DDP-debug scripts, and one hand-copied
  absolute path in `plan/pipeline_plan.md`) — all fixed via
  `privacy_redact(paths=["workspaces/megatron_deepspeed/20260726_185517/final_report"])`
  plus one manual sed for the path that predated the tool's redaction pass.
  Re-scan confirmed clean (77 files scanned).
- **Unrelated pre-existing finding, NOT part of this session:**
  `privacy_scan()` with no args resolves to a *different*, already-committed
  reference tree (`good-runs/1000genome/run-1/final_report/...`) that still
  has real leaks (flux job ids, `tuolumne####` hostnames, a username in a
  Lustre path) predating this session. Flagged to the user rather than fixed
  here — out of scope for a report-agent pass on this session, needs its own
  privacy-guard pass.

### Tool-gap note (not fixed this session, flag for future improvement)
`session_final_report`'s automatic script-collection glob only searches
`tmp/*<run>*.sh`-style names; it misses scripts that live in the session's
top-level `scripts/` dir under names that don't match a `tmp/`-run pattern
(this session's `run_opt_*.sh`, `optimizer_*.sh`, `ds_config_*.json` all live
directly under `<WS>/scripts/`, not `<WS>/tmp/`). Confirm this is worth a
generic MCP-tool fix (walk `<WS>/scripts/` too, not just `<WS>/tmp/`) before
persisting — propose, don't self-apply, per the confirmation-gate rule.

### Remaining work (STEP 11 privacy-guard + any future resume)
- Run `dftracer-privacy-guard` as the formal final pipeline step (this
  report-agent pass already did the practical privacy_scan/redact work above;
  a dedicated STEP 11 pass can re-verify).
- FlashAttention build+measure; num_workers>0 DataLoader overlap at
  production scale; checkpoint I/O (never triggered, save-interval 10 > 3-20
  iters); dftracer_service node-counter bracketing (rule 12, never applied —
  memory dimension has zero real HBM/occupancy telemetry as a result); BERT
  pretraining path (never started).
