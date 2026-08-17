---
name: project-laghos-papi-counter-cycling-variorum
description: Laghos PAPI counter-cycling + Variorum power on Tuolumne — 9 exact-count runs (30/30 counters), 9/9 rc=0, GPU power 176W mean, combined into one verified 7.5M-event compacted trace; fixed a variorum-linkage heap corruption first
metadata:
  type: project
---

Cycled Laghos through PAPI counter groups on the dftracer
`feature/variorum-power-tracing` branch, with Variorum node power collected
alongside every run, then combined everything into one compacted trace.

**Result: 9/9 runs rc=0, 30/30 counters covered, all counts EXACT.**

4 nodes x 16 ranks x 16 GPUs, `-p 1 -dim 3 -rs 3 -tf 0.10`, ~285 s per run.
Every run: 16 app + 4 service traces, 0 truncated, `multiplex: 0` on every PAPI
record, exactly its expected counter count, and 1,100-1,200 Variorum power
records. GPU power mean **176 W** (range 60-194).

Combined compaction: 180 input files (9 x 20) -> 11 chunks, **7,505,601 events,
173 MB, `Verification: PASSED`** (input hash == output hash), 0 truncated.
Categories include `papi` 384,416 and `gpu` (power) 10,448 alongside CPP_APP,
KERNEL_DISPATCH, HIP_RUNTIME_API, MPI and the node counters.

**A heap-corruption bug had to be fixed first** — variorum was being linked into
every traced app, loading two rocm_smi ABIs and aborting all ranks at exit even
with tracing disabled. See
[[bug-dftracer-variorum-linked-into-app-heap-corruption]].

**Why:** requested as "get available counters, cycle through them on different
runs, enable Variorum for power, make sure nothing fails, then create one common
compacted trace across all runs".

**How to apply:** the method, the verified 9-group partition, the Variorum build
requirements and the combined-compaction pitfalls are all in [[workload-laghos]].
Two silent traps that cost real time are in [[system-tuolumne]]: bash's special
`GROUPS` variable eats a run-matrix array, and a shared allocation with orphaned
`dftracer_service` daemons contaminates node-counter traces. Key measurement
lesson: PAPI presets share native events, so which subsets fit in hardware must
be PROBED, not computed from counter counts — all 7 BRANCH presets fit in 5
registers while FLOP fits only 3 of 8.

Artifacts: per-run traces in `traces/papi_<group>/`, combined set in
`traces/papi_all_compact/`, scripts `run_papi_cycle.sh` and
`compact_all_papi_runs.sh`. Not done: the final_report has not been regenerated
against these runs.
