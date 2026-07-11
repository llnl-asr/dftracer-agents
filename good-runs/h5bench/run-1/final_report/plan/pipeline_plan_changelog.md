# Pipeline Plan Changelog

## 2026-07-10 — 4th allocation added, slot map 9→13

- New flux allocation `<flux-jobid>` confirmed RUNNING, 32 nodes, ~24h remaining.
- Slot map extended: slots 10-13 = `<flux-jobid>` chunked into 4× 8-node/768-rank sub-blocks,
  same pattern as `<flux-jobid>`/`<flux-jobid>`. Total concurrent 8-node slots: 9 → 13.
- Recomputed wave estimates:
  - STEP 4 baseline (35 runs minimum): 4 waves → 3 waves (`ceil(35/13)=3`).
  - STEP 6 optimization (up to 145 runs): 17 waves → 12 waves (`ceil(145/13)=12`).
  - Total pipeline wave estimate: ≈21 waves → ≈15 waves.
- Rule unchanged: never submit two jobs into the same slot before the first completes; track
  occupancy via `flux jobs -no "{id} {state}"` per slot.

## 2026-07-10 — dftracer-optimizer (STEP 6) exhaustive 3-axis + literature pass
- DECISIVE: collective-metadata-ops L2 lever is INERT for read/append/overwrite — called unconditionally in baseline source set_pl() (read.c L400-401, append.c L490-491, overwrite.c L466-467), not gated on COLLECTIVE_METADATA config. Recorded to opt KB (workload scope). Real bottleneck is POSIX file-open()/stat storm at 768 ranks, untouched by HDF5 collective metadata ops.
- write_unlimited: re-verified all 5 reps uniformly ~6.3MB/1536 files — systemic incompleteness, NOT a rep1 fluke. Flagged do-not-optimize; needs config/trace re-check by tracer owner.
- L3 Lustre striping recorded INERT for metadata (MDS not OST); /p/lustre5 already PFL+DoM. Runs confirmed on $LUSTRE_ROOT/h5bench_baseline/.
- Built full 10-row cited proposal table (opt_proposal_table, 10 accepted/0 uncited) incl. all rejected/deferred options with reasons. Appended to optimization_plan_draft.md.
- No scale runs launched this pass (top lever already baseline; only non-inert levers require a rebuild coupled to the in-flux write config). Handoff iteration documented: meta_block_size + page-buffered file-space strategy.

## 2026-07-10 — Scope narrowed to read+write; read bug found+fixed; write.cfg recalibrated
- User directive: narrow active work to `read`+`write` only; deprioritize the other 5 workloads
  (leave their trace/data state as-is, no further action).
- Found and fixed a real bug in `read`: baseline reps 1-5 ran `h5bench_read` against a
  nonexistent file (silent HDF5 error-storm, exit 0). Added a write-then-read two-phase script
  and a matching `write_for_read.cfg`; DIM_1 shrunk 33554432->2097152. New traces:
  `read_v2/rep1-5`, confirmed 0 HDF5 errors.
- Recalibrated `write.cfg`: DIM_1 33554432->2097152 (/16), confirmed via real `du -sh` (not
  trace-log size, which was a misleading proxy) + real flux elapsed time: 575 GB actual,
  12.13 min actual (job f84WCJW3a7). New traces: `write_v2/rep1-5`.
- Diagnosed both (dfanalyzer POSIX preset): write 272.1 MB/s (INTERLEAVED/INTERLEAVED, ~1MB
  avg transfer) vs read 2403.8 MB/s (CONTIG/CONTIG) at the same rank count/system - write's
  access pattern is the dominant bottleneck. Draft L1/L2/L3 optimization plan appended to
  `optimization_plan_draft.md` (top lever: switch write.cfg to CONTIG/CONTIG, untested this
  pass; secondary: ROMIO cb_nodes+striping per KB flash_x precedent, since write IS
  bandwidth-bound unlike the metadata-bound read/append/overwrite shape).
- Retracted a prior-pass KB finding: the "read is metadata-time-bound, collective-metadata-ops
  inert" result was measured against the broken (errored) read run and is not valid for real
  read I/O. append/overwrite portions of that KB entry are unaffected (their logs showed 0
  HDF5 errors).
- Cleaned up ~510k loose/un-segregated stray trace files (~1.2GB) from `baseline/traces/raw/`
  root via `drm`, after explicit user confirmation they were retry debris.
- Incident: runaway job-submission loop (~3,200 queued jobs across 5 allocations) occurred and
  was cancelled by the coordinator; new mandatory occupancy-check rule added to `flux-alloc`
  skill going forward.
