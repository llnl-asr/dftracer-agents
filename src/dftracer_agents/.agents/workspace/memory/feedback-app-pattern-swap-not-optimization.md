---
name: feedback-app-pattern-swap-not-optimization
description: Never treat an app request/transfer-size or access-pattern sweep as "the optimization" — it's a diagnostic bound, not a system fix
metadata:
  type: feedback
---

**What happened:** During the IOR HDF5 optimization session (`ior/20260710_172024`), an early optimization pass reported bumping IOR's own `-t` transfer size from 4k to 4m (+190% write bandwidth) as "the optimization." The user corrected this: changing the app's own request/transfer size (or flipping its access pattern, e.g. INTERLEAVED→CONTIG) is a workload-pattern swap, not a system-level optimization — it changes what the app actually does rather than making the system serve the SAME real pattern faster, and can silently break correctness since the pattern is often load-bearing.

**Why:** A measurement like "4MB transfers get more bandwidth than 4KB transfers" is a valid DIAGNOSTIC characterization (it bounds how much headroom the small-request pattern leaves on the table) but must never be reported as the "best config" or credited as an optimization win. The real deliverable when every system-level lever (ROMIO data sieving/collective buffering, Lustre striping, etc.) is neutral-to-negative is the honest negative result itself — reporting "no available system-level lever beats the baseline for this access pattern, and here is why (root-caused to file-per-process contiguous I/O + Lustre client-side coalescing)" is a complete and valuable finding, not a failed optimization loop.

**How to apply:** Before crediting any optimization result, check whether the applied change altered the app's own request size / access pattern / data volume — if so, relabel it as a diagnostic characterization (headroom bound), not an optimization, and keep searching system/software-level levers with the pattern held fixed. This standing rule was persisted into the `dftracer-optimizer` agent template and the `dftracer-io-optimization` skill this session (`agents_sync` re-rendered afterward).

Related: [[feedback_data_cleanup_quota]].
