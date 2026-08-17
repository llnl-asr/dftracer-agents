---
name: project-minife-papi-sweep-variorum
description: miniFE PAPI counter sweep on Tuolumne — all 30 presets captured EXACTLY across 8 runs with variorum GPU power; found and fixed a dual-librocm_smi64-ABI heap corruption; combined compacted trace of 3.67M events
metadata:
  type: project
---

miniFE `openmp45-opt` on Tuolumne MI300A, 4 nodes x 4 ranks, `nx=768`, traced with
dftracer built from the local project tree at `feature/variorum-power-tracing`
(`v2.1.1-28-g3035768`) with PAPI + variorum + HIP + MPI.

**Result: all 30 PAPI presets captured, every one EXACT (`multiplex=0`), plus variorum GPU
power on all 32 service traces. 8 runs, 3,674,055 events in one combined compacted trace.**

## The counter partition took two attempts — sizing by name count is wrong

`papi_avail` reports 30 presets but only **5 hardware counters**. The obvious partition
(30 / 5 = 6 runs of 5 names) is WRONG, because a *derived* preset (`papi_avail` "Deriv
Yes") expands into more than one native event. Measured: two of the six sets silently
multiplexed (`args.multiplex == 1`, i.e. scaled estimates), and dftracer **dropped
`PAPI_BR_PRC` outright** — it is "one native event subtracted from another", and
time-sharing it could report a negative count. That run still exited 0 with 16/16
full-size traces.

Fix: split those two sets into four, giving 8 sets total, verified by cheap 1-rank
`nx=64` probes (~1 s each) reading `args.multiplex` back out before committing to full
runs. Final: 30/30 counters, `multiplex=0` everywhere. Full method in [[software-papi]].

## Dual `librocm_smi64` ABI = heap corruption (found, fixed)

Every run aborted at teardown with `corrupted size vs. prev_size in fastbins` (exit 134)
after printing correct results. It reproduced at 1 rank in seconds and was **independent
of every runtime flag** — that was the tell: the cause was linked, not executed.

Variorum pulls `librocm_smi64.so.7` (ROCm) into `libdftracer_core`, while hwloc's plugin
brings `librocm_smi64.so.1`. Different SONAMEs, both mapped, **374 identical exported
symbols** — ELF interposition sends all calls into one copy while each keeps its own
state, so allocations cross layouts. Fixed by the project's own commit `3035768`, which
links variorum only into the service that calls it. After it: one ABI in the app, exit 0.
Detail in [[system-tuolumne]].

The service binary still legitimately links both ABIs (it needs variorum) — the same
hazard remains inside the service process and is worth watching.

## Verified, not assumed

- **Trace completeness = trailing `end` event**, not file size. All 128 app traces
  `end`-terminated. This is what proved the earlier SIGABRT was strictly post-flush and
  the data usable. See [[dftracer-trace-utils]].
- Counter names present were compared against the requested set per run — that is how the
  dropped `PAPI_BR_PRC` was caught.
- Combined compacted trace 3,674,055 events == app 2,099,062 + service 1,574,993 summed
  independently, so the merge is lossless.

**Why:** requested as "get available counters and cycle through them on different runs
with different counters, enable Variorum for power too".

**How to apply:** load [[software-papi]] for the counter method, [[workload-minife]] for
the app, [[system-tuolumne]] for the ABI bug and the PAPI version pin.

Limitation: variorum built only the **AMD_GPU** power domain — E-SMI is not installed, so
there are no CPU power domains. Verify with the build log line
`variorum power domains to build: ...`.

Own mistake worth not repeating: a running shell script was edited in place, and bash —
which reads scripts incrementally by byte offset — resumed at the wrong offset and
executed garbage. It happened after the runs completed so no data was lost. Copy to a new
name instead.
