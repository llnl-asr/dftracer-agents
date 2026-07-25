# pipeline_plan changelog

## 2026-07-24 — STEP 8 (dftracer-optimizer, 4-way dispatch) completed
- Walked all four component checklists (io/compute/communication/memory) against
  the STEP 7 bottleneck list. `Task` subagent spawning was unavailable in the
  execution context, so the orchestrator walked each skill's exhaustive checklist
  directly; every L1/L2/L3 candidate is represented (applied/measurable AND
  not-applicable-with-reason), per Pipeline Policy rule 14.
- Produced merged 15-row citation-backed proposal table via `opt_proposal_table`
  (15 accepted, 0 rejected). Full report written to
  `<WS>/STEP8_optimization_report.md`.
- NO optimization variants were run at smoke scale (single-GPU, 10 synthetic
  SMILES, 23 s job — not production-representative). All rows are proposals for
  STEP 9 to measure. No `opt_kb_record` entries (no measured before/after).
- Priority order (weighted score): (1) bf16 mixed precision, (2) torch.compile,
  (3) larger batch, (4) fused attention, (5) activation checkpointing,
  (6) DataLoader num_workers, (7) dataset pre-tokenize/cache, (9) expandable_segments.
  Not-applicable: pin_memory (unified APU HBM), NUMA binding (inert x2 on this
  system per KB), Lustre striping (already PFL+DoM optimal), all comm levers
  (world_size=1 — deferred to STEP 9 multi-node).

### STEP 9 hand-off (updated)
STEP 9 must move OFF smoke scale to prove any optimization. Smallest meaningful
validation config:
1. **First, disambiguate the 70% gap** (not an optimization): one longer single-GPU
   run (more steps + real/larger dataset slice) or a rocprof pass to split
   one-time startup/import/HIP-context-init from per-step GPU kernel time. This
   decides whether compute levers even have headroom to move.
2. **Minimum multi-GPU DDP scale to activate communication**: 1 node x 4 MI300A
   GCDs (world_size=4), OR 2 nodes x 4 = 8 ranks to also exercise inter-node
   Slingshot-11/RCCL allreduce. world_size>1 is REQUIRED for any comm lever
   (#10/#11/#13) to be measurable at all.
3. **Real dataset slice** (not 10 synthetic SMILES) large enough that I/O op-count
   and DataLoader-worker overlap are exercised — enough samples for >=1 full epoch
   of steps at production batch.
4. **Fixed-work discipline**: same fixed epoch/step count, dataset size, batch,
   checkpoint interval across baseline and every variant; >=1 baseline replicate +
   >=1 best-variant replicate; node-counter service (`session_service_start/stop`)
   bracketing every launch. Apply levers ONE AT A TIME in table order, measure,
   `opt_kb_record` each, then combine non-conflicting winners for the final config.
5. Recommended cheapest-signal first pass: bf16-mixed (#1) at 1N x 4-GPU with a
   real dataset slice — highest weighted score, single-variable, and simultaneously
   makes the comm dimension measurable.
