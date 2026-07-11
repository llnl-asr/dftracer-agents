---
name: project-h5bench-read-write-optimization
description: h5bench read+write baseline fix + ROMIO cb_nodes+striping confirmed win (+490.5%) on Tuolumne, plus the comparator-corroboration methodology lesson
metadata:
  type: project
---

## State
Session narrowed scope to `read`+`write` workloads only (5 other h5bench workloads baselined
earlier, left as-is). Both `read` and `write` baselines were found broken/oversized and fixed:
- `read`: h5bench_read needs a pre-existing file matching its config dims; original baseline
  pointed it at a fresh empty path, producing a silent HDF5 error-storm that still exited 0.
  Fixed with a write-then-read two-phase script + a paired write-setup config.
- `write.cfg`: DIM_1 was 16x oversized (verified via real `du` on the actual output file, not
  trace-log size, which is a misleading proxy — trace logs stay small regardless of true data
  volume). Shrunk /16, re-verified at 768 ranks: ~575GB/12min per rep.

Optimization pass (7 levers tested, L1/L2/L3/network/memory axes, literature-backed):
- **CONFIRMED WIN**: ROMIO `cb_nodes=16` + `CRAY_CB_NODES_MULTIPLIER=2` + `lfs setstripe -c16
  -S4M` on write -> +490.5% median POSIX bandwidth, non-overlapping 5-rep ranges vs baseline,
  ~8.7x faster wall time, comparator-corroborated. Same lever/mechanism that won on a different
  workload on this system previously — now confirmed transferring across workloads.
- REJECTED (fundamental incompatibility, caught pre-scale): HDF5 paged file-space strategy
  cannot coexist with `COLLECTIVE_METADATA=YES` (HDF5 1.14.x restriction).
- REJECTED (implementation gap, caught pre-scale): naive `madvise(MADV_HUGEPAGE)` on a
  `malloc()`'d buffer always fails (alignment requirement) — would need an aligned allocator.
- INCONCLUSIVE x3 (network NIC/rendezvous tuning, isolated rendezvous threshold, NUMA
  cpu-affinity): each showed a large, misleading 5-rep median "improvement" (+38-68%) that a
  same-rep `comparator` cross-check revealed as noise (≤3% per-rep, flagged negligible).

All results (win/rejected/inconclusive) recorded to `dftracer-optimization-kb` with honest
verdicts in the notes field. `workload-h5bench` skill updated with: the confirmed ROMIO win as
the new write default, the paged-file-space incompatibility, the read write-first requirement,
and the DIM_1 real-du-verification-required lesson.

## Why
Baseline data must be trustworthy before any optimization claim means anything — a silently
broken benchmark (read) or a 16x-oversized run (write) would have invalidated every downstream
comparison. The multi-lever optimization pass established that this workload's aggregate-
bandwidth metric on shared multi-tenant Lustre is dominated by contention noise (CV 30-64%
routinely) — a 5-rep median alone is not sufficient evidence of a real effect; only the levers
whose per-rep `comparator` cross-check also showed a real, mechanistically-explained signal
(non-negligible, ideally with significant sub-metric deltas like open()/lseek()/lxstat()
latency) should be trusted.

## How to apply
For any future h5bench (or similarly noisy shared-filesystem) optimization pass: run the full
5-rep median/CV comparison AND a same-rep `comparator` cross-check before claiming any win —
if the comparator shows a negligible delta on the controlled single-rep pairing, do not trust
the median delta even if it looks large. This generic lesson is now also recorded in
`dftracer-optimization-kb` Rule 5 (comparator corroboration required). For h5bench specifically:
always verify a workload's real output size via `du` on the actual file (never trust dftracer
trace-log size as a size proxy — logs stay compact regardless of true data volume), and always
grep h5bench_write's run log for `HDF5-DIAG` blocks before trusting a "successful" run (its
`H5Fcreate_async` return value is never checked, so a failed create silently cascades into a
fabricated-looking "successful" performance summary on a 0-byte file).

See [[feedback-h5bench-session-incidents]] for the runaway-job-submission and cleanup-pattern
notes from this same session.
