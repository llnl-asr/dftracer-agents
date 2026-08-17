---
name: project-laghos-papi-dftracer-baseline
description: Laghos (MFEM/HIP) dftracer baseline on Tuolumne — COMPLETE at 4 nodes/16 GPUs with PAPI (family-grouped layout) + working ROC-profiler + node counters; 4 dftracer bugs fixed; final_report regenerated against the new layout (unvalidated)
metadata:
  type: project
---

Laghos (CEED/Laghos @ a2ad322, per llnl/ice4hpc_data apps_per_machine/tuolumne/laghos)
annotated with dftracer FUNCTION mode and baseline-traced on Tuolumne MI300A with
PAPI counters, ROC-profiler GPU tracing, and the dftracer_service node-counter
daemon.

**Status: BASELINE COMPLETE on the family-grouped PAPI layout.**

4 nodes x 16 ranks x 16 GPUs, `-p 1 -dim 3 -rs 3 -tf 0.15 -pa -d hip`, `-g1 -c23`:
340 s / 628 steps, `Energy diff 6.90e-06`, **rc=0, 0 truncated**. 828,320 app
events (16 ranks); compacts with the node-counter traces to 1,090,911 events in
3 chunks / 40.8 MB, 0 truncated. Streams: `papi` 295,218 (6 families x 49,203
each); `CPP_APP` 150,956; `KERNEL_DISPATCH` 131,804; `HIP_RUNTIME_API` 45,635;
MPI p2p/comm/collective 15,754/9,647/8,928.

**Four dftracer bugs found and fixed** — rocprofiler registration race and the
PAPI-sampler teardown SIGSEGV (see
[[bug-dftracer-rocprofiler-configure-race-and-papi-sampler-segv]]), the missing
include guard in `generic_function.h`, and the PAPI probe baking rocprofiler log
text into `dftracer_config.hpp`. Six MCP tool bugs also fixed, notably
`clang_annotate_project` reporting `ok, functions: 0` on unparseable C++ (see
[[bug-clang-annotator-silent-zero-functions]]).

**Counter-layout result (measured, two otherwise-identical runs):** grouping the
30 counters into 6 families gives **5.04x fewer PAPI records** and -56.3%
uncompressed PAPI payload, but only **-9.8% on gzipped disk** — gzip had already
deduplicated the old per-record envelope. No runtime cost (343 s -> 340 s), no
information lost. Detail and the two measurement traps in [[workload-laghos]].

**Why:** requested as annotate + baseline with dftracer and its service, PAPI on,
ROC profiler on if the app has GPU code; then "fix the rocm bug", rerun at 4 nodes
using as many resources as the app can, always selectively aggregate `dur < 1000`,
compact app+service together, produce a final report, and finally re-measure after
the counter layout changed to family grouping.

**How to apply:** load [[workload-laghos]] first — pinned toolchain, the expected
`-x hip` link failure, clang `compile_flags` requirement, FINI-on-every-exit, the
measured 4-node config, mandatory aggregation settings, and the counter-layout
numbers. [[system-tuolumne]] has the PAPI 7.2.0.2 pin (7.3.0.1 SIGSEGVs), the
`-c 23` core reservation, and the stale-daemon `stop`-before-`start` rule.

Remaining: analysis, diagnosis and the 4-dimension optimization loop NOT started.
`final_report/` is regenerated against the new layout and passes its completeness
+ README checks, but is **validated=False** — `scripts/run_all.sh` has never been
run from a clean directory. Note `session_final_report(overwrite=True)` regenerates
`config.ini` and `install.sh`, discarding manual additions to them (had to re-add
`ICE4HPC_PREFIX` and re-fix an absolute path in an install.sh comment afterwards).
Known minor open bug: `dftracer_service stop` leaves per-node gzip streams
unterminated (compaction repairs them).
