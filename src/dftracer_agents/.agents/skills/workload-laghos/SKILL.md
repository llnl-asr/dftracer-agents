---
name: workload-laghos
description: Build/annotate/trace caveats for Laghos (CEED/Laghos), the MFEM-based high-order Lagrangian hydrodynamics miniapp, as deployed by llnl/ice4hpc_data on Tuolumne (MI300A/HIP). Covers the prebuilt-dependency toolchain pin, the expected makefile link failure, the annotation include-flag requirement, and the FINI-on-every-exit fix.
---

# workload-laghos

Laghos (https://github.com/CEED/Laghos) solves the time-dependent Euler
equations in a moving Lagrangian frame with high-order FEM, built on **MFEM**.
It is a *traced scientific workload* (hence `workload-*`, see
`feedback-software-vs-workload-naming`), GPU-capable through MFEM's device
backend (`-d hip`), and does **no application file I/O** in its solve loop —
the interesting signal is compute + MPI, not I/O.

Deployment reference: `llnl/ice4hpc_data` →
`apps_per_machine/tuolumne/laghos/{README.txt,makefile,makefile-serial}`.
Pinned commit used there: `a2ad322` (2025-05-09).

## Toolchain is pinned by the PREBUILT dependencies — do not use the site default

Laghos links prebuilt MFEM 4.8.1 / Hypre / METIS 5.1.0 / Umpire from a shared
prefix (`$ICE4HPC_PREFIX`, e.g. a group-readable dir on the site PFS). Those were
built with `cray-mpich-9.0.1-rocmcc-6.4.2-cce-20.0.0-magic`, so the session MUST
use the same compilers, **not** Tuolumne's `systems.yaml` default
`PrgEnv-cray`/`cce`. Mixing produces libstdc++/ROCm ABI mismatches at link time.

```bash
module load rocm/6.4.2
module load rocmcc/6.4.2-cce-20.0.0-magic   # auto-replaces cce/20.0.0
module load cray-mpich/9.0.1
module load cmake/3.29.2
export CC=.../cray-mpich-9.0.1-rocmcc-6.4.2-cce-20.0.0-magic/bin/mpiamdclang
export CXX=.../cray-mpich-9.0.1-rocmcc-6.4.2-cce-20.0.0-magic/bin/mpiamdclang++
```

Membership in the group owning `$ICE4HPC_PREFIX` is required to read it; without
it, every dependency has to be built from source per the README (Umpire → Hypre
→ METIS → MFEM), which is hours of work.

## The makefile link rule is EXPECTED to fail — use the README's explicit link

MFEM's installed `config.mk` carries HIP compile flags including **`-x hip`**,
and `-x <lang>` is *sticky* in clang. The makefile's link rule passes the `.o`
files to `$(MFEM_CXX)`, which therefore treats `laghos.o` as HIP **source**:

```
laghos.o:1:108: error: source file is not valid UTF-8
   1 | <U+007F>ELF...
76 warnings and 20 errors generated when compiling for gfx942.
make: *** [makefile:115: laghos] Error 1
```

This is not a broken build — compilation of the `.o` files succeeded. Let `make`
fail, verify the objects exist, then link with the explicit command from the
ice4hpc README. In a script: `set +e` around `make` (with `set -o pipefail`,
`make ... | tail` otherwise aborts the script before the fallback link runs).

Two more source prep steps the README requires before compiling:
- `laghos.cpp` ships with CRLF + non-ASCII bytes the clang frontend rejects:
  `dos2unix laghos.cpp` then `LC_ALL=C tr -cd '\0-\177' < laghos.cpp > tmp && mv tmp laghos.cpp`.
- MFEM's `config.mk`/`test.mk` contain CMake generator expressions GNU make
  cannot parse, plus a bare `dl`; flatten with the README's two `sed` lines.

## Annotation: clang MUST get the project's include flags

Every function worth tracing in `laghos_solver.cpp` and `laghos_assembly.cpp` is
an **out-of-line class method** (`void LagrangianHydroOperator::Mult(...)`).
Without include paths, `#include "general/forall.hpp"` is a *fatal* clang error,
the AST is empty, and those methods vanish — `clang_annotate_project` then
reports `ok` with `functions: 0` and the whole file is silently left
un-annotated (measured: 0 of 15 solver functions).

Pass `compile_flags` to `clang_annotate_project` / `clang_extract_functions`:

```
-std=c++17 -D__HIP_PLATFORM_AMD__=1 -DUSE_PROF_API=1 -DCAMP_HAVE_HIP -DFMT_HEADER_ONLY=1
-I$ICE4HPC_PREFIX/include -I$ICE4HPC_PREFIX/include/mfem
-I$ROCM_PATH/include -I$ROCM_PATH/include/rocrand
-I<cray-mpich ...-rocmcc-...>/include
-I<session venv>/lib/python3.13/site-packages/dftracer/include
```

Derive these from the build itself rather than guessing — MFEM's
`MFEM_INCFLAGS` plus the makefile's `EXTRA_INC_DIR` (`include/mfem`) plus the
MPI wrapper's include dir. **Strip `-x hip` and `--offload-arch=` from
`MFEM_INCFLAGS`** before handing them to the extractor. With the right flags:
15/22 solver functions and 5/11 assembly functions annotate cleanly.

The cost heuristic skips `LagrangianHydroOperator::Mult` and
`AssembleForceMatrix` because they mostly delegate. Add both by hand — `Mult` is
the per-timestep ODE RHS (the natural parent span for SolveVelocity/SolveEnergy)
and `AssembleForceMatrix` wraps one of laghos's two named timers. The C++
`DFTRACER_CPP_FUNCTION()` guard is RAII, so `AssembleForceMatrix`'s early
`return` needs no explicit END.

## MANDATORY: `DFTRACER_CPP_FINI()` on EVERY exit of `main`, or you get no app events

`main()` in `laghos.cpp` has **five** exits (`return 1` twice, `return 3` twice,
and the normal `return 0`). The annotator inserted `REGION_END`+`FINI` on only
the *first* early-error path it found, so on the normal path finalize never ran,
the logger never flushed, and the trace contained **zero** `CPP_APP` events —
while still showing plausible POSIX/STDIO/PAPI activity, because brahma's GOTCHA
interception and the PAPI sampler flush independently of the annotation logger.
That combination reads like success and is very easy to miss.

Always grep for the balance before building, and place `FINI` *before* any
`MPI_Finalize()`:

```bash
grep -n "DFTRACER_CPP_REGION_START\|DFTRACER_CPP_REGION_END\|DFTRACER_CPP_FINI" laghos.cpp
awk -v s=<main_start> 'NR>=s && /return /' laghos.cpp   # every one needs END+FINI
```

After the fix, a 1-rank smoke went from 0 → 24,123 `CPP_APP` events.

## Run configuration (measured, Tuolumne MI300A)

4 GPUs per node → 4 ranks per node, `-g1` per rank. `-d hip` is required to use
the GPU; confirm via laghos's own `Device configuration: hip,cpu` line.

Measured baseline, 1 node / 4 ranks, annotated + PAPI + service daemon:

| setting | value |
| --- | --- |
| args | `-p 1 -dim 3 -rs 2 -tf 0.6 -pa -d hip --no-visualization` |
| wall time | ~370 s (560 steps) |
| correctness | `Energy diff: 6.90e-06` |
| app trace | ~30 MB/rank gz, 10.96 M events total |
| service trace | ~2 MB, 93.6 k node-counter events |

Trace composition (dominated by MPI, as expected for this workload):
`p2p` 4.89M, `comm` 3.67M, `collective` 1.40M, `CPP_APP` 206K, PAPI 431K,
`POSIX` 164K, `STDIO` 161K. Per-step function counts scale as expected
(`Mult` 9,072 = 560 steps × 4 ranks × RK stages).

For a quick smoke use `-p 0 -dim 3 -rs 1 -tf 0.05` (seconds).

## Laghos does NOT need HDF5

`hdf5_in_source` is false; detection nonetheless auto-enables a system HDF5 if
one exists. Pass `hdf5=False` to `session_install_dftracer` so dftracer is not
built against an HDF5 the app never calls.

## Detection false-negative: `hip: false`

`session_detect` greps the app's own source for HIP and finds none — laghos
reaches the GPU only through MFEM's `MFEM_FORALL`. Pass `hip=True` and
`rocm_path=/opt/rocm-6.4.2` explicitly (detection otherwise picks the newest
available ROCm module, which will not match the app's).

See also: [[system-tuolumne]] (PAPI version pin, ROCm/rocprofiler notes),
[[software-hdf5]], [[dftracer-annotation-lessons]].

## Verified 4-node baseline (2026-08-13, all fixes in)

`-p 1 -dim 3 -rs 3 -tf 0.15 -pa -d hip`, 4 nodes x 16 ranks x 16 MI300A GPUs,
`-g1 -c23` per rank:

| item | value |
| --- | --- |
| wall time | 343 s (628 steps) |
| correctness | `Energy diff: 6.90e-06` |
| exit | rc=0, 0 truncated traces |
| app events | 2,024,590 over 16 ranks |
| service events | 259,787 over 4 nodes |
| compacted | 3 chunks, 43 MB, 2,284,377 events, 0 truncated |

Streams: PAPI 1,487,850; GPU 179,947 (all 5 kinds); `CPP_APP` 151,274;
MPI `p2p` 16,027 / `comm` 9,760 / `collective` 9,713; `POSIX` 8,737.
Per-rank function counts: `Mult`/`SolveVelocity`/`SolveEnergy` 40,640 each.

Sizing model, measured untraced at `rs=3` / 16 ranks: `tf=0.03` -> 335 steps in
101 s. Scale linearly from that; `rs=3` roughly triples step count vs `rs=2`
because the finer mesh shrinks the CFL timestep.

**Always enable selective aggregation for this workload.** With HIP tracing on,
`HIP_RUNTIME_API` alone emits thousands of sub-microsecond events per step per
rank, so an unaggregated laghos run is enormous: a single 4-rank run measured
10.96 M events / ~30 MB per rank.

**Use `dur < 100` (microseconds).** That is the threshold for any run whose
purpose is per-event detail — a trace corpus, a kernel/comm timeline, an overlap
analysis:

```bash
export DFTRACER_ENABLE_AGGREGATION=1
export DFTRACER_AGGREGATION_TYPE=SELECTIVE     # UPPERCASE -- see below
export DFTRACER_AGGREGATION_FILE=<ws>/scripts/aggregation.yaml
# aggregation.yaml:  inclusion: ["dur < 100"]
```

`dur < 1000` is a **size-first** setting, not the default. Measured here it cut an
identical workload from 165,710 to 10,726 events — a 93.5% reduction, 1.49 MB ->
178 KB — with every category still *represented*. But at 1000 us essentially every
GPU dispatch and MPI operation in laghos is sub-threshold, so what survives is a
count-and-duration summary of exactly the events the trace was collected to show.
Reach for it only when total corpus size is the binding constraint and per-event
GPU/MPI detail is expendable; `dur < 100` is otherwise the right choice, and is
what the tuolumne genesis sweep uses.

`DFTRACER_AGGREGATION_TYPE` is compared **case-sensitively** against
`"SELECTIVE"`; anything else (including lowercase `selective`) silently falls back
to `FULL`, which aggregates EVERYTHING and throws away all per-event detail.
Available rule fields: `cat`, `name`, `ts`, `dur`, `app`, `rank`, `tags.<name>`.
The `dftracer` metadata category is always exempt from aggregation.

App and service traces are written to the SAME directory on purpose, so a single
directory-scoped `dftracer_split` compacts both in one pass — and it repairs the
service traces' unterminated gzip streams as a side effect (`stop` leaves them
without an end-of-stream marker).

## PAPI family-grouped counter layout: 5x fewer records, but only ~9% smaller on disk (2026-08-13, measured)

dftracer's probe can emit counters grouped by family
(`CACHE:PAPI_L1_DCM,...;FLOP:...;CYCLE:PAPI_TOT_CYC`) instead of a flat list. The
runtime then writes ONE record per family per sample, `cat="papi"`, `name=<FAMILY>`,
with that family's counters in `args` as `X` and `X_delta`.

Measured on two otherwise-identical Laghos 4-node/16-rank runs (`rs=3 tf=0.15`,
both rc=0, both untruncated, 343 s vs 340 s so no runtime cost):

| metric | per-counter | per-family | change |
| --- | --- | --- | --- |
| PAPI records | 1,487,850 | 295,218 | **-80.2% (5.04x)** |
| app events total | 2,024,590 | 828,320 | -59.1% |
| **app bytes (gz)** | 36,872,607 | 33,246,036 | **-9.8%** |
| compacted bytes (gz) | 44,699,401 | 40,760,563 | -8.8% |
| uncompressed PAPI payload/rank | 17,272,294 | 7,552,448 | **-56.3%** |
| bytes per counter | 185.8 | 81.9 | **2.27x better** |
| counters carried/rank | 92,970 | 92,190 | unchanged |

5.04x measured vs 5.00x predicted (30 counters / 6 families). All six families
(CACHE, TLB, BRANCH, FLOP, INSTRUCTION, CYCLE) come out at exactly 49,203 records
— a useful sanity check that grouping is actually active.

**Do not report this as "5x smaller traces".** On disk it is ~9%. The old format
repeated a near-identical envelope (`name`/`cat`/`ts`/`ph`/`pid`/`tid`/`hhash`/
`multiplex`) 30x per sample, which gzip already deduplicated very well; the new
format removes 4 of 5 envelopes but replaces them with long, distinct JSON key
names that compress worse per unit of information. The remaining PAPI cost is
dominated by key names (~26 of 81.9 bytes/counter is the name, repeated as `X` and
`X_delta`). `value` and `_delta` are NOT redundant — 99.7% of pairs differ; only
the first sample has them equal by definition.

**Verification gotcha:** the grouped layout uses a single lowercase `papi`
category. Analysis scripts that look for the old uppercase per-family categories
(`CACHE`, `FLOP`, ...) as top-level `cat` values will report **zero PAPI events**
on a perfectly good trace.

**Comparability warning:** raw `service_*.pfw.gz` sizes are NOT usable for
before/after comparisons unless each run had a clean single daemon lifecycle. A
stale/duplicate `dftracer_service` keeps appending to the same per-host path; one
such file spanned 1,202,637 s (~14 days) at 6 events/s versus 469 s at 284
events/s for a clean run, which would have looked like a 98% "improvement" that
had nothing to do with the change under test. Service categories (`sys`/`io`/`net`)
never go through the PAPI path, so a counter-layout change cannot move them.

## PAPI counter cycling for EXACT counts, + Variorum power (2026-08-13, measured)

The node exposes 30 programmable presets but only **5 hardware registers**, so
asking for all 30 at once forces multiplexing and every value becomes a
time-sliced estimate. Cycling counter subsets across runs gets exact counts
instead. Select per run with `DFTRACER_PAPI_EVENTS=<comma list>` and
`DFTRACER_PAPI_MULTIPLEX=0`.

**Do not derive the subsets by counting names.** PAPI presets share native
events, so the naive "count <= num_hwctrs" answer is wrong in both directions.
Measured on MI300A (PAPI 7.2.0.2): all **7 BRANCH** presets fit in 5 registers,
while **FLOP fits only 3 of 8** and **CACHE 7 of 11**. Probe it — add each event,
`PAPI_start`, `PAPI_stop` — and pack greedily.

Verified family-coherent partition, 30 counters into **9 exact runs**:

| run | n | counters |
| --- | --- | --- |
| cache_p1 | 7 | L1_DCM, L2_DCM, L2_ICM, L2_TCM, L2_DCH, L1_DCA, L2_DCR |
| cache_p2 | 4 | L2_ICH, L2_ICA, L2_ICR, L2_TCH |
| tlb | 2 | TLB_DM, TLB_IM |
| branch | 7 | BR_UCN, BR_CN, BR_TKN, BR_NTK, BR_MSP, BR_PRC, BR_INS |
| flop_p1..p4 | 3/2/2/1 | FMA+FP+VEC / FML+FAD / FDV+FSQ / FP_OPS |
| inscyc | 2 | TOT_INS, TOT_CYC |

A greedy family-*mixed* pack needs only 8 runs, but each trace then mixes
cache+TLB+branch and is far harder to read. One extra run buys interpretability.

Measured result, 4 nodes x 16 ranks, `rs=3 tf=0.10`, ~285 s per run, **9/9 rc=0**:
every run had 16 app + 4 service traces, 0 truncated, `multiplex: 0` on every
record, and exactly its expected counter count. Combined: 30/30 counters covered.

An explicit `DFTRACER_PAPI_EVENTS` list carries no family information, so it is
emitted as a single family named `PAPI` under `cat: "papi"`. **The run identity
must come from the trace directory, not the category** — all nine runs look
identical by category alone.

### Variorum power

Power is node-wide, so it is collected by `dftracer_service`, not the app, and
lands in the per-node service traces as `type: 13`, `ph: 2`, `name: "power"`,
`cat` = family (`gpu`/`cpu`/`memory`/`node`). Nothing to switch on at run time.
Build with `DFTRACER_ENABLE_VARIORUM=ON` and **`DFTRACER_BUILD_VARIORUM=ALWAYS`**
— a distribution variorum on MI300A reports `_ERROR_VARIORUM_UNSUPPORTED_PLATFORM`
and silently yields no power at all.

On Tuolumne expect the **AMD_GPU domain only**: `librocm_smi64` is present so the
GPU domain builds, but there is no E-SMI, so AMD CPU power is silently omitted.
Measured: ~1,100-1,200 power records per run, mean **176 W** per GPU (range
60-194), keyed `socket_<n>.GPU_<n>` plus `num_gpus_per_socket`.

### Combining runs into one compacted trace

`dftracer_split` is directory-scoped, so stage every run's traces into one
directory and compact once. **Rename the service traces on the way in**: app
traces are uniquely named per run, but every run writes
`service_<hostname>.pfw.gz` and the runs share hosts, so copying them flat makes
each run silently overwrite the previous run's node counters and power. Prefix
with the group name.

Correct flags are `--output` and `--chunk-size` (MB) — `--output-dir` /
`--chunk-size-mb` do not exist and make the tool print usage and **exit 0**,
which reads as a successful no-op. Measured combined result: 180 input files
(9 x 20) -> 11 chunks, 7,505,601 events, 173 MB, `Verification: PASSED` with
matching input/output hashes, 0 truncated.
