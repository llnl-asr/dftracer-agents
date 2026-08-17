---
name: project-minife-papi-hip-dftracer-baseline
description: miniFE openmp45-opt (OpenMP 4.5 GPU offload) annotate + full-feature dftracer baseline on Tuolumne — 4Nx4R nx=768 traced with PAPI + HIP + node-counter service; PAPI teardown segfault is the one open item
metadata:
  type: project
---

miniFE (Mantevo @ abe3288, per llnl/ice4hpc_data apps_per_machine/tuolumne/miniFE),
variant **openmp45-opt** (OpenMP 4.5 target offload to gfx942), annotated with dftracer
FUNCTION mode and baseline-traced on Tuolumne MI300A with PAPI hardware counters, HIP/GPU
tracing, and the per-node dftracer_service daemon.

**Status: baseline COMPLETE, all requested features working. One open dftracer bug.**

dftracer built from the LC-GitLab branch `feature/papi-counter-tracing` with PAPI + HIP +
MPI on, HDF5 off. Verified at artifact level, never from the install's exit status:
`DFTRACER_PAPI_TRACING_ENABLE 1` (30 counters detected, 5 hw / 7 fitting),
`DFTRACER_HIP_TRACING_ENABLE 1`, `DFTRACER_MPI_ENABLE 1` / CRAYMPICH, HDF5 undef, and
`ldd libdftracer_core.so` showing libpapi.so.7.2 + librocprofiler-sdk.so.0 (ROCm 6.4.2).

Measured baseline (4 nodes x 4 ranks = 16 ranks, nx=ny=nz=768, 200 CG iterations, 3m45s):
**1,165,631 events** — PAPI 738,930 (CACHE/FLOP/BRANCH/TLB/INSTRUCTION/CYCLE), MPI 83,557
(p2p/collective/comm), HIP 32,553 (KERNEL_DISPATCH/MEMORY_COPY/PAGE_MIGRATION), CPP_APP
18,789 across all 20 annotated functions, POSIX/STDIO 53,074, plus 233,310 node-counter
events from dftracer_service on 4/4 nodes (sys/io/net). App and service traces are
co-located in the session's `traces/baseline/`.

**HIP tracing works here** — not because OpenMP offload is special, but because the
`rocprofiler_configure` registration fix landed (see [[system-tuolumne]]).
`HIP_RUNTIME_API` and `SCRATCH_MEMORY` are legitimately empty: miniFE reaches the GPU
through the OpenMP target runtime and makes no HIP runtime calls of its own. This
supersedes the "HIP blocked" reading in [[project-laghos-papi-dftracer-baseline]].

**Open item — PAPI teardown SIGSEGVs 1-3 ranks of 16.** The crash happens AFTER all
science completes, so those ranks never flush (13/16 flushed in the delivered baseline).
A/B isolated on identical pinned hosts and arguments: PAPI on -> segfault (13/16, 14/16,
15/16 over three runs, different rank each time); `DFTRACER_ENABLE_PAPI_TRACING=0` ->
clean 16/16; pristine un-annotated binary -> clean; cutting `DFTRACER_PAPI_EVENTS` to 5
fitting counters -> still segfaults, so it is not multiplexing. Duration-dependent: clean
at nx=256 (17 s), crashes on ~4-minute runs.

This is NOT a new bug — it is the already-root-caused PAPI-sampler-vs-GOTCHA teardown race
in [[bug-dftracer-rocprofiler-configure-race-and-papi-sampler-segv]], whose fix has **not
landed on `feature/papi-counter-tracing`**. That branch demonstrably carries the *other*
fix from the same work (`nm -D --defined-only libdftracer_core.so` exports
`rocprofiler_configure`, and GPU tracing works) while still reproducing this crash, so
"has PAPI support" does not imply "has the teardown fix". Check the ordering in
`DFTracerCore::finalize()` on whatever branch you install.

**Why:** requested as "annotate and run miniFE again, use gitlab branch
feature/papi-counter-tracing and run dftracer_service with as much features as u can like
HIP and PAPI", as a 4-node job inside a shared larger allocation that another session was
also using.

**How to apply:** load [[workload-minife]] first — it has the five-variant table, the
pinned ice4hpc toolchain (rocmcc/6.4.2-cce-20.0.0-magic + mpiamdclang++, NOT the
site-default PrgEnv-cray), the mandatory basic/fem/utils symlinks, the `make clean` trap
that deletes the checked-in `miniFE_info.hpp`, the 24-function annotation table with its
hot-loop exclusion list, and the `verify_solution` cap that makes miniFE return 1 at every
benchmark size on the PRISTINE binary too. [[system-tuolumne]] has the PAPI version pin
(7.2.0.2, never 7.3.0.1) and the host-pinning rule for dftracer_service.

Two tool bugs found and FIXED this session (MCP server restart required to load them):
the clang annotator's END/FINI-after-return placement (see
[[bug-clang-annotator-silent-zero-functions]]) and `session_install_dftracer` omitting
`papi` from its reported `features_enabled` even when PAPI compiled in correctly.

Traces are split/compacted into `traces/baseline_split/` (5 chunks at 4 MB), verified
**lossless**: 1,165,631 events and identical per-category counts across all 20 categories
vs the raw directory. Two verification notes worth reusing:

- `event_count` is **approximate** — it prefixes its result with `~` and reported
  1,165,641 for the split vs 1,165,631 raw. That 10-event gap is the tool's estimate, not
  data loss; confirm with a per-category parse before treating any delta as a real leak.
- `stats --report summary` on this directory reports `Events Scanned: 0` / `Categories (0)`
  — the known indexer bug in [[bug-dftracer-stats-categories-zero-events]] is still
  present, so category counts must come from a direct gzip+json parse.

Not done: dfanalyzer analysis, diagnosis, optimization loop, final_report. This session
stopped at a verified, split baseline.
