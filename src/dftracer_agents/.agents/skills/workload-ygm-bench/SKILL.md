---
name: workload-ygm-bench
description: >
  ygm-bench (github.com/llnl/ygm-bench) specific knowledge: build against an
  annotated YGM tree, the hot-loop over-annotation/selective-aggregation fix,
  MPI_Iallreduce rank-density collapse, irecv buffer OOM tuning, and the
  confirmed >=10-minute scaled dftracer run configuration on Tuolumne. Load
  this skill whenever running or tracing ygm-bench (as opposed to building
  YGM itself — see [[software-ygm]] for that).
---

Cross-references: [[software-ygm]] [[dftracer-annotation-lessons]] [[system-tuolumne]] [[tools-dftracer]] [[software-mpi]]

`ygm-bench` is the traced BENCHMARK/workload here, the same role IOR or
h5bench play for their respective libraries — see [[workload-ior]] /
[[workload-h5bench]] for the analogous pattern. [[software-ygm]] is the
library it exercises and is annotated by dftracer; this skill is about
building, scaling, and running that benchmark, not about YGM's own
annotation/build internals.

---

## Build: against THIS session's annotated YGM (not a fresh copy)

`ygm-bench`'s `CMakeLists.txt` tries `find_package(ygm CONFIG)` first,
falling back to `FetchContent_Declare(ygm GIT_REPOSITORY ...)` if that fails.
The annotated YGM tree has no `install()`/`export()` rules, so `find_package`
never succeeds — it would silently fall through to fetching a FRESH,
UNANNOTATED ygm from GitHub. Fix: configure ygm-bench with

```
-DFETCHCONTENT_SOURCE_DIR_YGM=<ws>/annotated/source
```

which makes `FetchContent_MakeAvailable(ygm)` use the local annotated tree in
place instead of git-cloning — no CMakeLists.txt patch needed in either repo.
Because annotated YGM's own `CMakeLists.txt` links its `dftracer_core_imported`
target into the `ygm` INTERFACE target, ygm-bench's own
`target_link_libraries(... ygm::ygm)` transitively picks up the dftracer link
— confirmed via `ldd <binary> | grep dftracer`. See
[[dftracer-annotation-lessons]] LESSONS_LOG.md 2026-08-04 for the general
FETCHCONTENT_SOURCE_DIR_<NAME> pattern (applies beyond just YGM).

ygm-bench's own benchmark entry points (`around_the_world_ygm.cpp`,
`barrier_bench.cpp`, ...) each need their own explicit
`DFTRACER_CPP_INIT()`/`DFTRACER_CPP_FINI()` added to `main()` — without it,
every `DFTRACER_CPP_FUNCTION()` reached transitively through YGM is a silent
no-op because dftracer was never initialized in that process. A header-only
library's internal annotation is never self-sufficient — every CONSUMING
application needs its own INIT/FINI too.

The prerelease wheel (see [[tools-dftracer]]) does not ship `dftracer_service`
— if the node-counter daemon is required, install a second from-source
dftracer for that binary while still linking the app against whichever
dftracer install matches its own compiler ABI.

Confirmed working small run: `around_the_world_ygm -n 50 -t 2 -p` and
`barrier_bench -b 500 -a 0 -t 2 -p`, 1 node / 4 ranks, GNU toolchain. Trace
category breakdown (one representative rank file, 8,222 events): `CPP_APP`
3686 (annotated comm functions), `p2p` 3361 (`MPI_Testsome`/`MPI_Test`/
`MPI_Irecv`/`MPI_Isend`), `env` 772, `collective` 264
(`MPI_Iallreduce`/`MPI_Allreduce`), `POSIX` 69, `dftracer`(meta) 32, `STDIO`
22, `comm` 16 — confirms real annotated comm/collective activity dominates,
not init/POSIX noise.

## Hot-loop over-annotation collapsed traced performance at scale (critical, 2026-08-04)

The initial annotation pass instrumented `ygm::comm::local_progress()` and 8
sibling functions on YGM's async progress-polling path (see
[[dftracer-annotation-lessons]] CP10 for the full mechanism): `local_progress`,
`local_wait_until`, `process_receive_queue`, `flush_next_send`,
`check_completed_sends`, `check_if_production_halt_required`,
`post_new_irecv`, `local_process_incoming`, `handle_completed_send` — all
reachable from one shared progress-poll path. At 128-rank scale (ygm-bench's
`around_the_world_ygm`) this made traced runs 5-10x+ slower than untraced,
with wall time independent of `-n` (workload size) — the tracing overhead
itself, not the communication pattern, dominated.

**Final fix used (not a full strip): DFTRACER selective aggregation.** Kept
all 9 functions annotated in YGM and instead enabled, when RUNNING ygm-bench:
```bash
export DFTRACER_ENABLE_AGGREGATION=1
export DFTRACER_AGGREGATION_TYPE=SELECTIVE
export DFTRACER_AGGREGATION_FILE=<path-to-rules.yaml>
export DFTRACER_TRACE_INTERVAL_MS=5000
```
`rules.yaml`:
```yaml
inclusion:
  - "dur < 1000"
exclusion: []
```
See [[dftracer-annotation-lessons]] CP10 for the full rule-syntax reference.
This restored normal near-linear scaling AND kept per-function visibility
(real `dft_cnt` call counts on aggregated `CPP_APP` events) at a fraction of
the trace volume: the confirmed final 128-rank/11-min run (`-n 5600`, 716,800
total hops) produced only ~55MB across 128 rank files, vs. >1GB PER RANK in
the earlier unaggregated attempt. Don't default to stripping annotation
entirely from a traced benchmark's hot path — aggregation is almost always
the better tradeoff when the hot function's own behavior is diagnostically
relevant.

## MPI_Iallreduce collapses badly at high per-node rank density (Cray MPICH, Tuolumne)

`around_the_world_ygm`'s termination-detection protocol issues thousands of
`MPI_Iallreduce` calls. Measured with `DFTRACER_ENABLE=0` (untraced, so this
is a real MPI/system finding, not a tracing artifact):
- 4 ranks/1 node: 510K hops/sec
- 32 ranks/1 node: 1,229 hops/sec (~400x collapse), `MAX_WAITSOME_IALLREDUCE`
  = 130.2s of 130.1s total wall time — essentially ALL time is collective wait.

This means per-node rank density is a first-order performance variable for
ygm-bench (and likely any workload with a similar Iallreduce-heavy
termination protocol) on Tuolumne's Cray MPICH — don't assume "more ranks =
proportionally more work done." 32 ranks/node was usable; 96 ranks/node
combined with this collapse made runs take tens of minutes even for modest
workloads (separately from the buffer-memory OOM risk below).

## irecv buffer memory: OOM at high rank density with defaults, deadlock/extreme-slowdown if cut too aggressively

Default `YGM_COMM_NUM_IRECVS=8` × default irecv size (1GB) = 8GB/rank of
receive buffers. At 96 ranks/node that's ~768GB, causing OOM
(`memory.peak ≈ 476.87G`, consistently hit across multiple nodes/attempts —
looked like the same cgroup memory ceiling every time). Cutting the buffer
size via env var to reduce this must use the CORRECT variable name:
**`YGM_COMM_IRECV_SIZE_KB`** (singular "IRECV") — NOT `YGM_COMM_IRECVS_SIZE_KB`
(plural), which is only the JSON output's display label
(`comm_environment.hpp` line ~169), not what `std::getenv` actually reads
(line ~112). Setting the wrong (plural) name silently no-ops and the default
1GB/irecv stays in effect. Reducing to a too-small value (tested 64MB) at 96
ranks/node avoided the OOM but produced what LOOKED like a deadlock — low
memory usage (`flux exec free -g` showed ~6GB/501GB used) and no progress for
minutes — but was likely the same Iallreduce collapse taken further, not a
true deadlock; not fully confirmed given time constraints. 32 ranks/node with
DEFAULT buffer settings (1GB×8=8GB/rank×32=256GB/node) worked reliably with
no OOM and no apparent hang.

## Confirmed working scaled run (2026-08-04, reference config)

`ygm-bench/src/around_the_world_ygm -n 5600 -t 1 -p`, 4 nodes x 32 ranks/node
(128 total, GNU toolchain, single MPI runtime), full annotation + selective
aggregation (see above): completed in 660.04s (11.0 min), 716,800 total
hops, `MAX_WAITSOME_IALLREDUCE`=646s (confirms the Iallreduce-collapse
finding above as the genuine bottleneck, not a tracing artifact), 55MB
total trace data across 128 rank files. This is the reference config for
any future >=10-minute scaled ygm-bench dftracer run on Tuolumne:
- 4 nodes, 32 ranks/node (NOT 96 — Iallreduce collapse + OOM risk)
- default YGM buffer settings (`YGM_COMM_NUM_IRECVS`/`YGM_COMM_IRECV_SIZE_KB`
  unset)
- selective aggregation enabled as above
- `-n` sized ~5000-5600 for ~10-11 minutes at this scale; scale roughly
  linearly with `-n` once aggregation is enabled (confirmed: n=500 -> 70.2s,
  n=5000 -> 572s, n=5600 -> 660s at the same 128-rank config)
