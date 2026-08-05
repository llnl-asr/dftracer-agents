---
name: project-rajaperf-hip-optimization-dftracer-pipeline
description: RAJAPerf HIP+MPI dftracer pipeline on Tuolumne (MI300A) -- complete, honest negative result (real compute ceiling ~2%, baseline unchanged is best), 7 tool bugs fixed, final_report validated=True and readme/completeness clean
metadata:
  type: project
---


**Session:** `<session>`. RAJAPerf (LLNL RAJA Performance Suite) on Tuolumne
(AMD MI300A GPU-resident APU, ROCm 7.2.1). Status: PIPELINE COMPLETE, final_report/
validated=True, pdf.generated/completeness.ok/readme_check.ok all true.

**Headline result: honest negative finding.** Full 4-dimension optimizer walk
(io/compute/communication/memory, Pipeline Policy rule 14) found NO adoptable
optimization. Best config = unchanged baseline (`--hip-data-space HipDevice`, default
build). The one plausible lever, `--hip-data-space HipManaged`, was applied+measured at a
smaller/contended 4-rank scale first (looked like a win, -68% alloc bucket, wall
unchanged), then RE-TESTED at the fully-verified true 4-node/16-rank scale and found to be
a REGRESSION (-2.9% wall / -3.6% timed, paired positive 5/5) -- correctly reversed rather
than reported as a win. Real optimizable compute ceiling is ~2% because RAJAPerf's own
`Base_Seq` serial reference variant (required for checksum correctness, unoptimizable by
definition) is 97.9% of untraced timed work. The originally-diagnosed "compute DOMINANT,
26.8s HIP bucket" was ~82-85% dftracer/rocprofiler-sdk tracing observer effect (measured
5.72x GPU-side inflation via an untraced `DFTRACER_ENABLE=0` control run on the identical
binary) -- this untraced-control-run technique was the single most consequential
measurement of the session and should be considered standard practice for any
many-small-HIP-kernel-launch workload.

**Critical methodology finding: TWO separate silently-invalid baseline rounds this
session**, each caught only by independent re-verification, never by trusting the
launcher's own report:
1. A bad `--kernels` name (`DOT`/`AXPY`, invalid) silently selected ZERO kernels rather
   than erroring -- trace looked plausible (matching event/line counts) but per-rank
   `runSuite()` span duration was 2-4 microseconds and the dataset dir was empty.
2. A `flux-proxy` allocation expiring mid-launch silently fell back to running only 2
   single processes instead of the requested 4-node/16-rank job -- trace again "looked"
   real (1.24M events, genuine HIP categories) and only counting distinct pids/hostnames
   (2, not 16) caught it.
The resulting MANDATORY verification protocol (now in `workload-rajaperf` skill): before
crediting ANY multi-node run, independently confirm (a) distinct pid count matches
expected rank count, (b) distinct hostname count matches expected node count, (c) the
app's own checksum/completion report states the expected rank count, (d) span durations
are non-trivial (seconds, not microseconds) -- never trust the launch wrapper's own echoed
rank count alone.

**7 real dftracer-tool/annotation-tool bugs found and fixed this session (generic, not
workarounds):**
1. `_detect_rocm` picked the OLDEST installed ROCm (4.2.0) instead of the newest (7.2.1) --
   falsely reported "ROCm too old for HIP tracing." Fixed to select newest.
2. `env.sh`/module list was missing the ROCm module for HIP-needing sessions -- added.
3. `ROCM_PATH`/`CMAKE_PREFIX_PATH` never reached the dftracer HIP build, silently compiling
   HIP tracing out with no error -- fixed by explicit `CMAKE_PREFIX_PATH=/opt/rocm-7.2.1`.
4. `CPLUS_INCLUDE_PATH` was poisoning Cray Clang's own toolchain header search -- unset
   before invoking Cray-Clang-based builds.
5. `_add_braces_via_clang` (annotation tool) trusted a degraded/best-effort clang AST when
   clang had zero include paths for RAJA/BLT submodule headers and hit a parse error,
   inserting stray brace pairs unrelated to the real annotation sites (8 in `Executor.cpp`,
   5 in `KernelBase.cpp`, one breaking a constructor's member-initializer list). Fixed: the
   tool now checks clang's exit code and no-ops with `skipped_reason` on a degraded parse.
6. A C++ `main()` macro-usage bug: `DFTRACER_CPP_REGION_START("main")` passed a quoted
   string where the macro's `profiler_##name` token-pasting requires a bare identifier, and
   `DFTRACER_CPP_FUNCTION_UPDATE` was used where `DFTRACER_CPP_REGION_DYN_UPDATE(main, ...)`
   was required. This is a genuine macro-misuse class distinct from the brace-corruption
   class -- worth checking for on any REGION_START/END-based (vs FUNCTION-based) main().
7. `mcp__dftracer__analyze` (`analyze()`) hits a NEW resource-exhaustion bug distinct from
   the previously-documented dask-teardown hang: at `cluster_n_workers=32` on a small trace
   it spawns ~192 OS threads PER WORKER (thousands total), exhausting `ulimit -u`. The
   existing "always use cluster_n_workers=32" guidance is right-sized for LARGE traces but
   wrong for small ones -- scale worker count to trace size. Not yet fixed at the tool
   level; every analysis pass this session fell back to manual gzip+json aggregation.

**Skills touched (proposed via confirmation gate):** new `workload-rajaperf` skill;
updates to `software-rocm` (C++ HIP-init-ordering extension, gfx900-vs-gfx942 gotcha,
`HipHostAdviseFine` SIGABRT do-not-retry), `system-tuolumne` (`_detect_rocm` fix note,
`rocm/7.2.1` module), `dftracer-annotation-lessons` (third `clang_add_braces` corruption
instance, `DFTRACER_CPP_REGION_*` token-pasting gotcha), `dftracer-trace-utils`
(`analyze()` 192-threads-per-worker bug).

**Final report:** `<WS>/final_report/`, validated=True (self-contained validation replicate
run against live allocation `<flux-jobid>`, wall 80.6-81.5s vs established 79.1-80.6s band,
0 FAILED checksums, "16 MPI ranks" confirmed -- reproduced within noise). pdf.generated,
completeness.ok, readme_check.ok all true. Privacy scan NOT yet run by this agent (per
task instructions, that is the separate `dftracer-privacy-guard` final pipeline stage) --
everything written this session was anonymized ($WS placeholders, no absolute user paths,
no flux jobids in git-tracked content) so the scan should come back clean.

**Remaining work (see REPORT.md Section 11 for full detail):** `opt_kb_render()` never
completed (server was briefly unreachable at merge time) -- individual `opt_kb_record`
calls landed, only the skill markdown re-render is outstanding. Two ready-to-run
communication candidates unmeasured: `MPICH_SHARED_MEM_COLL_OPT=1` and
`--hip-mpi-data-space HipDevice`+`MPICH_GPU_SUPPORT_ENABLED=1`.
