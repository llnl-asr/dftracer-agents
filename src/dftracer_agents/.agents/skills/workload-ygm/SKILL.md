---
name: workload-ygm
description: DEPRECATED — content split between [[software-ygm]] (the YGM library itself, annotated BY dftracer) and [[workload-ygm-bench]] (ygm-bench, the traced benchmark that exercises YGM — analogous to workload-ior/workload-h5bench). This stub exists only to redirect old references.
metadata:
  type: software
---

**This skill is deprecated and split in two.** Load whichever fits:

- **[[software-ygm]]** — the YGM library itself: annotation caveats
  (header-only + split `.ipp`, include order), build/CMake linkage to
  dftracer, Cray-clang-vs-spdlog compiler pitfall, smoke test. YGM is
  annotated BY dftracer, not a traced scientific workload, hence `software-*`
  naming per [[feedback-software-vs-workload-naming]].
- **[[workload-ygm-bench]]** — ygm-bench (github.com/llnl/ygm-bench), the
  actual traced BENCHMARK run against the annotated YGM tree: build steps,
  the hot-loop over-annotation + selective-aggregation fix,
  `MPI_Iallreduce` rank-density collapse, irecv buffer OOM/env-var-name
  pitfall, and the confirmed >=10-minute scaled-run reference config. This
  follows the same `workload-*` pattern as `workload-ior`/`workload-h5bench`
  — ygm-bench is a benchmark workload, not a library dftracer instruments.

Originally reconciled 2026-08-04 during the ygm/20260804_221748 session's
final report pass into a single `software-ygm` file; split again the same
day per user correction (ygm-bench deserves its own `workload-*` skill, same
as IOR/h5bench, rather than being folded into the library's skill). Do not
re-add content here — edit `software-ygm` or `workload-ygm-bench` going
forward, matching which one actually owns the fact.
