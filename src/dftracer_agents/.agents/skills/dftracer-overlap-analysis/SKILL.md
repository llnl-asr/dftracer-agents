---
name: dftracer-overlap-analysis
description: How to correctly measure temporal overlap between two trace categories (e.g. compute vs data-loading, data vs communication) across MULTIPLE PROCESSES — a dynamic-interval, event-splitting algorithm that avoids both the "same-pid sweep-line gives a false 0%" artifact and the ">100% overlap" bound-invalidity bug. Load this skill whenever asked "does X overlap with Y", "how much is compute/GPU stalled waiting on I/O or comm", or any data-stall / pipeline-overlap question.
---

Cross-references: [[dftracer-diagnoser]] [[dftracer-trace-utils]] [[dftracer-io-optimization]]

## Always query with the `view` MCP tool — never hand-parse `.pfw.gz` files

Per the mandatory `dftracer-trace-utils` rule: extract the events you need with
`mcp__dftracer__view` (query DSL, `output_file=` to avoid flooding context on large pulls),
never `gzip`/`grep`/manual JSON parsing. Example, pulling everything needed for a compute-vs-
data overlap analysis in one call:

```
view(directory="<compact_trace_dir>",
     query='cat == "compute" OR cat == "communication-io"',
     output_file="<scratch>/events.ndjson")
```

Then load the NDJSON with pandas/json for the analysis below. This is fast (bloom-filter
pruned) even against traces with 10s of millions of events for OTHER categories you didn't ask
for — the `view` tool only returns matching events.

## Two wrong ways to compute overlap, and why they fail

1. **Per-process (same-pid) sweep-line interval-merge is NOT what you want for
   cross-process pipelines.** If `compute` and `communication-io` (data-loading) are annotated
   on the SAME process's timeline (the training-loop's own `dft_event_logging` calls), they
   will NEVER intersect on that pid alone — a single-threaded loop is definitionally serial on
   its own timeline. This produces a spurious "0% overlap" result that says nothing about
   whether DataLoader WORKER processes are actually prefetching concurrently with the main
   process's compute. Measured on PECAN (2026-07-22): exact same-pid sweep-line gave 0.00s
   overlap before AND after a real prefetch fix that measurably improved wall time — a strong
   tell that the metric, not the app, was broken. See [[software-pecan]] for the full incident.
2. **Naive bucket-bound overlap with `interval_width` too small relative to event
   durations is not just imprecise, it produces IMPOSSIBLE results (>100% overlap).** The
   pigeonhole bound `overlap(bucket) = max(0, busyA + busyB - bucket_width)` is only valid when
   no single process's per-bucket busy time can exceed `bucket_width` — which requires either
   (a) `bucket_width >= 2 * max(event_duration)` so no event can span more than one bucket
   boundary in a way that inflates a single bucket's count past its own width, or (b) events are
   explicitly SPLIT/clipped at bucket boundaries. Skipping both and using a small bucket width
   (e.g. a percentile like p99 instead of the true max, to get finer resolution) lets any event
   longer than that percentile get fully attributed to one bucket, pushing that bucket's busy
   time past `bucket_width` and silently invalidating the subtraction — measured on PECAN: p99-
   sized buckets without event-splitting gave **overlap_pct = 204.75%**, a nonsense number that
   is itself the tell something is wrong, not a result to report.

## The correct algorithm (validated 2026-07-22, PECAN baseline5)

1. **Pull all events for the two categories** (`catA`, `catB`) via `view`, across ALL
   processes in the trace (not filtered to one pid) — `(pid, cat, ts, dur)` per event.
2. **Choose the interval/bucket width.** Using the TRUE max event duration
   (`interval_width = 2 * max(dur)`) guarantees correctness (Rule 2 above) but can collapse
   resolution to just a couple of buckets if there's a rare outlier event (e.g. an init/warmup
   span) — measured on PECAN: one 64.2s outlier event forced `interval_width = 128.4s` against
   a 144s job, giving only 2 buckets. **Prefer a percentile (p99 worked well) for resolution,
   but ONLY if you also do step 3 (event-splitting) — otherwise you hit the >100% bug above.**
3. **MANDATORY if using anything less than the true max: split every event that crosses a
   bucket boundary into per-bucket partial durations**, attributing to each bucket only the
   fraction of the event's `[ts, ts+dur]` interval that actually falls inside it. This keeps
   every per-process per-bucket busy time bounded by `bucket_width`, which is what makes the
   pigeonhole subtraction in step 5 valid regardless of interval size.
4. **Per bucket, per category: take the MAX per-process summed busy time across ALL
   processes**, not the sum across processes and not a single pid's value — this is what
   correctly captures "was ANY process of category A busy" vs "was ANY process of category B
   busy" in that window, which is exactly the cross-process (main-loop vs DataLoader-worker)
   overlap question, regardless of whether A and B ever share a pid.
   `busyA(bucket) = max_over_pid(sum of catA event durations for that pid in that bucket)`,
   same for `busyB`.
5. **Per bucket: `overlap(bucket) = max(0, busyA(bucket) + busyB(bucket) - bucket_width)`**
   (pigeonhole lower bound — valid because step 3/4 bound both terms by `bucket_width`).
   Sum over all buckets: `total_overlap = Σ overlap(bucket)`.
6. **Compute the percentage** relative to whichever total is more meaningful for the question
   being asked: `overlap_pct_of_A = total_overlap / total_A * 100` (e.g. "what % of compute
   time is overlapped with data-loading"), and the complement
   `stall_pct_of_A = 100 - overlap_pct_of_A` (e.g. "what % of compute time is NOT hidden behind
   data-loading — i.e. actual compute stall"). Also report `overlap_pct_of_B` and
   `overlap_pct_of_smaller_total = total_overlap / min(total_A, total_B) * 100` as
   cross-checks — none of these three percentages should ever exceed 100 with correct
   event-splitting; if one does, the splitting step has a bug, re-check it before trusting the
   number.

## Worked example — PECAN compute vs data-loading (baseline5, 2026-07-22)

`p99_dur = 0.412s` → `interval_width = 0.824s` → 169 buckets (vs. only 2 with the true-max
sizing) → `total_A(compute) = 114.14s`, `total_B(comm-io) = 106.28s`,
`total_overlap = 81.92s` → **overlap_pct_of_compute = 71.8%**, i.e. **compute stall ≈ 28.2%**
of compute time is NOT hidden behind data-loading. This superseded an earlier same-pid
sweep-line result that had (wrongly) reported 0% overlap for the same trace — see
[[software-pecan]] for the full before/after comparison and the reversal it caused.

## When this becomes a permanent tool feature vs. a one-off script

This algorithm is generic, deterministic logic that should live in a shared tool rather than be
re-derived by hand each session (Pipeline Policy rule 4). `dftracer`'s own analyzer library
(`dftracer/analyzer/metrics.py`) already has a RELATED but different mechanism
(`async_layers` config → `u_<layer>_time_proc` columns, diffed against `compute_time_proc`
per FIXED time-granularity bucket, no dynamic sizing or event-splitting) — see
`dftracer-diagnoser`'s rules for how to invoke that existing feature. The dynamic-interval +
event-splitting refinement in this skill is NOT yet implemented there; if this analysis is
needed repeatedly, propose (with user confirmation, per `feedback-confirm-before-skill-updates`)
extending `dftracer/analyzer/metrics.py` (or a new MCP tool) with this exact algorithm rather
than re-running a scratch script each time.

## Known gap: per-worker trace attribution does not survive `multiprocessing_context="spawn"`

**Naming convention, so you check the right filename pattern:** dftracer names each process's
raw trace file `<run_name>-<hash>-<app_name>.pfw.gz`. The main rank process (which calls
`initialize_log`/registers an app name) produces `-app.pfw.gz`. A Python DataLoader WORKER
process is a plain Python context with no registered app name, so its trace file has an EMPTY
name segment: `<run_name>-<hash>-.pfw.gz` (note the trailing `-.pfw.gz`, not `-app.pfw.gz`) —
**"worker trace" does not mean a `worker_N` subdirectory, it means these unnamed sibling
files sitting right next to the `-app.pfw.gz` files in the same flat `traces/raw/` directory.**

Verified directly on PECAN (2026-07-22), counting file suffixes in `traces/raw/`:
- `baseline5` (default `fork` context, `num_workers=2`, 16 ranks): **16** `-app.pfw.gz` files
  (one per rank) **+ 64** unnamed `-<hash>-.pfw.gz` files (the DataLoader workers — present and
  populated, as expected).
- `io_opt4` (`multiprocessing_context="spawn"`, `num_workers=6`, 16 ranks): **16**
  `-app.pfw.gz` files, **and ZERO** unnamed files — not just differently-named, genuinely
  ABSENT. The 96 spawned worker processes (16 ranks × 6 workers) produced no trace output at
  all under `spawn`.

This BLOCKS the compute-vs-data-loading overlap algorithm in this skill, which depends on
pulling events from every process INCLUDING workers (step 4's "max busy-time across ALL
processes" needs the worker processes' own events to exist at all). Root cause not yet
isolated — suspect dftracer's per-process trace-file bootstrap (env var, fd, or global state
set up before fork) doesn't get re-initialized in a freshly `spawn`ed process the way it does
in a `fork`ed copy-on-write child. **Before relying on this algorithm against any
`spawn`-context DataLoader trace, first check the raw trace directory for unnamed
`-<hash>-.pfw.gz` files** (`find <raw_trace_dir> -iname '*.pfw.gz' -not -iname '*-app.pfw.gz'`)
— if that count is 0, the overlap number cannot be computed at all for that run, and any
wall-time win from a `spawn`-based fix should be reported as unverified-mechanism until this
gap is fixed.

## 2026-07-22 update — the documented Hybrid-mode fix did NOT close the gap

Tried the fix pydftracer's own docs recommend for exactly this scenario ("Application
Instrumentation" note: "Since [DataLoader-style code] spawns separate Python processes for
parallel data loading, we use hybrid mode... to capture I/O from both the main process and
spawned workers") — `DFTRACER_INIT=HYBRID` + `LD_PRELOAD=libdftracer_preload.so` alongside the
existing FUNCTION-mode annotations, on the exact `io_opt4` config that gave PECAN's real -35.7%
`num_workers=6`/`spawn` win. Result: **still zero unnamed worker trace files** — identical
outcome to the un-preloaded run. `HYBRID` is not a recognized `DFTRACER_INIT` value in this
dftracer version's `ProfileInitType` enum (only `PROFILER_INIT_FUNCTION`/
`PROFILER_INIT_LD_PRELOAD` exist) and silently no-ops with no warning. An `LD_PRELOAD`-only
fallback (no `HYBRID`) was also tried and likewise produced zero worker traces, but additionally
hung indefinitely at process teardown (killed by timeout) — worse, not better. See the
`software-pecan` skill's corresponding entry for full details, including a real, separate
`libstdc++` RPATH bug this test surfaced in `libdftracer_preload.so` itself.

**Status: this gap remains fully open.** The -35.7% wall-time win from the spawn-based
`num_workers` fix is measured independently of tracing (from the app's own epoch-timer log) so
it stands, but its overlap mechanism is still unvalidated. Next things to try, not yet
attempted: `strace`/`ltrace` on a live spawned worker PID to see whether the preload
constructor even fires; check whether `DFTRACER_LOG_FILE`'s hash-based naming collides across
the parent and spawned children (same PID-derived hash landing in the same file, silently
overwriting instead of creating a new one); check dftracer's gotcha/brahma init-time debug
logging (`DFTRACER_LOG_LEVEL=DEBUG`) for any explicit skip/early-return in a spawned child.
