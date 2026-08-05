---
name: project-ygm-ygm-bench-dftracer-pipeline
description: YGM + ygm-bench dftracer pipeline on Tuolumne — annotate/build/scale complete, final_report assembled and privacy-clean, self-contained flux validation still outstanding
metadata:
  type: project
---

Session `ygm/<session>` annotated LLNL's YGM (header-only C++ MPI async-messaging
library, github.com/llnl/ygm) and its companion benchmark ygm-bench with dftracer
FUNCTION-mode C++ tracing (55 instrumentation points across `comm.ipp`,
`collective.hpp`, `mpi.hpp`, `comm_router.hpp`, `comm_environment.hpp`), then
scaled to a validated 4-node/128-rank run.

**Status: pipeline complete through final_report assembly; self-contained
flux-allocation validation NOT yet run** (deferred this pass due to time
budget — needs a `flux alloc -N4 -q pdebug -t 60m` + `bash scripts/run_all.sh
<alloc-id>` end-to-end run in an isolated `final_folder_validate/` dir before
`validated=True` can be claimed).

**Key findings (all confirmed via untraced control runs, so real MPI/system
behavior not tracing artifacts):**
- `MPI_Iallreduce` collapses ~400x with per-node rank density on Cray
  MPICH/Tuolumne (510K hops/sec @ 4 ranks/node -> 1,229 hops/sec @ 32
  ranks/node); `MAX_WAITSOME_IALLREDUCE` ~= 98% of wall time at scale. No
  application-level lever found this session; settled on 32 ranks/node.
- Annotating 9 hot-loop/progress-poll functions made traced runs 5-10x+
  slower and non-scaling with `-n` — fixed via DFTRACER SELECTIVE
  aggregation (`inclusion: ["dur < 1000"]`), not by stripping annotation:
  shrank per-rank trace size from >1GB to <1MB while keeping `dft_cnt`
  visibility.
- Default `YGM_COMM_NUM_IRECVS=8` x 1GB irecv buffers OOM at 96 ranks/node
  (~768GB/node > ~502GB available); correct fix env var is singular
  `YGM_COMM_IRECV_SIZE_KB`, not the plural `YGM_COMM_IRECVS_SIZE_KB` (which
  silently no-ops — it's only a JSON display label in `comm_environment.hpp`).

**Build chain:** prebuilt prerelease dftracer wheel had NO MPI support
compiled in; rebuilt from source (LLNL czgitlab) with
`DFTRACER_ENABLE_MPI=ON DFTRACER_MPI_IMPL=CRAYMPICH BRAHMA_MPI_VERSION=900001`,
using the GNU MPICH wrapper to match YGM's own `PrgEnv-gnu` toolchain (Cray-clang
20.1.6 fails compiling spdlog's bundled fmt with a consteval error — see
[[system-tuolumne]]). Building dftracer with crayclang+MPI while the app used
PrgEnv-gnu double-linked two MPI runtimes into one binary — a real crash risk,
corrected by rebuilding dftracer itself with GNU.

**Final validated run:** `around_the_world_ygm -n 5600 -t 1 -p`, 4 nodes x 32
ranks/node = 128 total, full annotation + selective aggregation: 660.04s
(11.0 min), 716,800 hops, 55MB traces across 128 rank files.

**Framework fix credited:** `session_build_annotated`'s `build_subdir` param
was silently ignored for cmake/autotools/meson builds — fixed in
`src/dftracer_agents/mcp_tools/tools/session/session_tools.py` (already
applied, this session just exposed it via YGM's nested `annotated/source/`
layout).

**Self-learning persisted this session:** merged `workload-ygm` (misnamed —
YGM is annotated BY dftracer, so `software-*` per
[[feedback-software-vs-workload-naming]]) into `software-ygm`, which is now
the canonical/complete skill; `workload-ygm` is now a deprecation stub.
`dftracer-annotation-lessons` CP9/CP10, `tools-dftracer` prebuilt-vs-source,
and `system-tuolumne` Cray-clang20/spdlog-fmt entries were verified already
complete, no edits needed. Two MCP tool bugs flagged but not yet fixed:
`mcp__dftracer__reader(mode="lines", start=0)` rejection, and
`mcp__dftracer__view` losing quotes on double-quoted DSL queries when routed
through this session's `flux proxy` wrapper — see
[[dftracer-trace-utils]].

**Open items / resume point:** ~580GB of superseded trace dumps
(`ygm_bench_scale/traces/`, `traces2/`) left in place — cleanup blocked by a
permission denial, needs user confirmation; no formal `dfdiagnoser`/
`comparator` pass was run against the final traces (both headline findings
are manually diagnosed from app-level timers, not tool-scored); the 4-dim
optimizer checklist (`dftracer-optimizer-io/-communication/-compute/-memory`)
was never dispatched this session. `final_report/` is assembled, PDF
generated, completeness/README checks pass, and both `final_report/` and the
touched skills are privacy-clean — but `validated=False` until the live
flux-allocation reproduction is actually run.
