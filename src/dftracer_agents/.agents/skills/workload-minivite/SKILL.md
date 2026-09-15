---
name: workload-minivite
description: miniVite (ECP-ExaGraph) — distributed-memory Louvain community-detection proxy app (C++11, MPI+OpenMP, plain Makefile). Where the real work actually lives (three headers, not main.cpp), which functions are per-vertex hot helpers that must NOT be annotated, how to size the synthetic RGG run, and the full-feature dftracer baseline measured on an MI300A/Cray system. Load this for any miniVite build, annotation, or trace session.
---

# workload-minivite

miniVite implements ONE phase of the Louvain method for graph community detection
in distributed memory. Upstream: `ECP-ExaGraph/miniVite`. See also
[[software-papi]], [[system-tuolumne]], [[dftracer-annotate-cpp]],
[[bug-clang-annotator-silent-zero-functions]].

## Source layout — the work is in HEADERS, not in `main.cpp`

The whole app is four files:

| file | content |
| --- | --- |
| `main.cpp` | `main()` + `parseCommandLine()` only — ~2 real functions |
| `dspl.hpp` | the entire distributed Louvain solver (~16 free functions) |
| `graph.hpp` | `Graph`, `BinaryEdgeList` (file input), `GenerateRGG` (synthetic input) |
| `utils.hpp` | LCG random generator, parallel prefix op, small helpers |

**`clang_annotate_project` discovers only `.c/.cpp/.cxx/.cc`, so on miniVite it
annotates `main.cpp` and NOTHING ELSE.** That yields a trace with ~2 app spans
per rank and no solver visibility at all — a green report over an effectively
un-instrumented app, the failure shape described in
[[bug-clang-annotator-silent-zero-functions]]. Annotate the three headers
explicitly with `clang_annotate_file(language="cpp", is_entry=False)`.

These are ordinary (non-template) inline functions, so clang parses them fine —
this is NOT the miniFE template-header case. But two flags are required:

```
-std=c++11 -fopenmp -DPRINT_DIST_STATS -I<mpi include dir> -include mpi.h
```

`-include mpi.h` is the non-obvious one: `dspl.hpp` and `utils.hpp` use
`MPI_Datatype`/`MPI_INT64_T` without including `<mpi.h>` themselves (they rely on
the including TU). Without the force-include, clang parses in error recovery and
you lose functions silently.

**Tool gap observed:** `compile_flags` reaches `clang_extract_functions` but NOT
the internal `clang_add_braces` pass, which therefore always reports
`brace insertion was SKIPPED (safety guard) ... clang exited non-zero`. For C++
this is harmless — RAII `DFTRACER_CPP_FUNCTION()` inserts only at
`body_first_line`, never an END before a braceless body — but on a C project the
same gap would matter.

**Cosmetic:** in a header with no `#include` before the first function, the tool
puts `#include <dftracer/dftracer.h>` on line 1, above the file's own comment
banner and include guard. Harmless (dftracer.h self-guards), but expect it.

## Functions that MUST be excluded — per-vertex hot helpers

`distLouvainMethod` runs

```cpp
#pragma omp parallel ... 
  for (GraphElem i = 0; i < nv; i++)
      distExecuteLouvainIteration(i, ...);   // ONCE PER LOCAL VERTEX PER ITERATION
```

and `distExecuteLouvainIteration` itself calls `distBuildLocalMapCounter` and
`distGetMaxIndex`. Annotating any of those three emits O(nv x iters) events per
rank from every OpenMP thread — gigabytes of noise. Always pass:

```
exclude_functions='["distExecuteLouvainIteration","distGetMaxIndex","distBuildLocalMapCounter"]'
```

The AST cost filter correctly drops the ~27 trivial `Graph` accessors
(`get_lnv`, `get_owner`, `local_to_global`, `get_edge`, ...) on its own.
`distUpdateLocalCinfo` / `distCleanCWandCU` are also auto-skipped as trivial —
leave them skipped; they are called once per thread per iteration.

The resulting good annotation set is 17 traced functions:
`main` (REGION), `parseCommandLine`, `GenerateRGG`, `Graph`, `generate`,
`set_nedges`, `print_dist_stats`, `distSumVertexDegree`,
`distCalcConstantForSecondTerm`, `distInitLouvain`, `distComputeModularity`,
`fillRemoteCommunities`, `updateRemoteCommunities`, `exchangeVertexReqs`,
`createCommunityMPIType`, `destroyCommunityMPIType`, `distLouvainMethod`.

`main` is a clean case for the annotator: a single `return 0;` preceded by
`MPI_Finalize()`, so `REGION_END -> FINI -> MPI_Finalize -> return 0` lands
correctly with no hand fixing. The `MPI_Abort` paths inside `parseCommandLine`
are argument-validation only.

## Build

Plain Makefile, `CXX = CC` (Cray wrapper) by default. Override on the command
line; it needs `-std=c++11` or newer, OpenMP, and MPI:

```bash
make -j 8 CXX="$CXX" \
  CXXFLAGS="-std=c++11 -g -O3 -fopenmp -DPRINT_DIST_STATS -I$DFT/include" \
  OPTFLAGS="-O3 -fopenmp -DPRINT_DIST_STATS -L$DFT/lib64 -ldftracer_core -Wl,-rpath,$DFT/lib64"
```

The Makefile's link rule is `$(CXX) $^ $(OPTFLAGS) -o $@`, so the dftracer link
flags go in `OPTFLAGS`, not `LDFLAGS`. The library is **`-ldftracer_core`** —
`-ldftracer` does not exist. `CXXFLAGS` in the Makefile expands `$(OPTFLAGS)`,
so overriding both on the command line is required, not just one.

One pre-existing warning (`fabs` on an integer in `utils.hpp`) is upstream and
harmless.

## Running

```
-n <vertices>   generate a synthetic RGG in memory — NO input file needed
-f <bin-file>   real graph, in Vite binary format
-l              use the distributed LCG instead of std:: RNG
-p <pct>        add random long-range edges
-t <threshold>  modularity convergence threshold
```

Constraints when using `-n`: **the process count must be a power of 2** and
**the vertex count must be divisible by the process count**.

On Cray, either export `MPICH_MAX_THREAD_SAFETY=multiple` at run time or build
with `-DDISABLE_THREAD_MULTIPLE_CHECK` — otherwise `MPI_Init_thread` fails the
`MPI_THREAD_MULTIPLE` check whenever `omp_get_max_threads() > 1` and the app
calls `MPI_Abort`.

### Run sizing: RGG GENERATION dominates, not Louvain

The parallel RGG generator is **O((nv/p)^2)** in its local edge search, so wall
time explodes with vertices-per-rank. Measured at 16 ranks (2 nodes x 8):

| `-n` | wall |
| ---: | ---: |
| 262,144 | 2.2 s |
| 1,048,576 | 17.7 s |
| 2,097,152 | **73 s** |
| 4,194,304 | > 10 min (killed) |

At `-n 2097152` the split was **63.1 s graph generation vs 0.18 s Louvain
solve** (20 iterations, modularity 0.753). So a `-n`-sized run is a *graph
generator* benchmark, not a Louvain benchmark. **If the Louvain phase is what
you want to trace or optimize, use a real input graph via `-f`, or push the rank
count up so `nv/p` stays small.** Do not size an RGG run by total wall time and
then attribute the time to community detection.

## Full-feature dftracer baseline (2 nodes x 8 ranks x 11 threads, MI300A/Cray)

`-n 2097152`, FUNCTION mode, rc=0, 73 s, 16/16 ranks flushed (matched
`start`/`end`, zero 0-byte traces), 98,010 events over 18 files.

| layer | cat | events | note |
| --- | --- | ---: | --- |
| app annotation | `CPP_APP` | 1,184 | 17 functions, `comp=` comm 800 / cpu 384 |
| POSIX | `POSIX` | 4,176 | mostly `access`, `opendir` |
| STDIO | `STDIO` | 29,292 | `fgets`/`feof` — dominated by `/proc` scans at init |
| MPI p2p | `p2p` | 7,580 | `MPI_Isend`/`Irecv`/`Waitall`/`Sendrecv` |
| MPI collective | `collective` | 1,280 | `Alltoall` 656, `Allreduce` 352, `Barrier` 176, `Reduce` 96 |
| MPI comm/datatype/env | `comm`/`datatype`/`env` | 64/112/96 | `Comm_rank/size`, `Type_create_struct`, `Wtime` |
| PAPI | `papi` | 10,112 | family-grouped, see below |
| node counters | `sys`/`io`/`net` | 32,204/5,644/996 | per-core CPU, per-NVMe, per-NIC |
| node power (variorum) | `gpu` type 13 | 166 | per-socket APU power |
| dftracer metadata | `dftracer` | 5,104 | incl. 7 app-metadata keys x 16 ranks |

**PAPI was EXACT.** `DFTRACER_PAPI_EVENTS=PAPI_TOT_CYC,PAPI_TOT_INS,PAPI_FP_OPS,PAPI_FP_INS`
gave `multiplex == 0` on all 10,112 records and all four requested counters
present — despite `PAPI_FP_INS` being a derived preset and the node having only
5 hardware counters. Read from the compute-node trace, per [[software-papi]]
Rule 4; a login-node probe can report a different multiplex value.

**No PAPI teardown SIGSEGV** on this czgitlab `develop` build — all 16 ranks
flushed, unlike the `feature/papi-counter-tracing` behaviour recorded in
[[system-tuolumne]].

**GPU tracing is legitimately ABSENT** (`KERNEL_DISPATCH`, `MEMORY_COPY`,
`HIP_RUNTIME_API` all zero): miniVite is a CPU-only MPI+OpenMP app and dftracer
was built `hip=False`. The `cat: "gpu"` type-13 records are **variorum node
power telemetry sampled by `dftracer_service`, NOT GPU kernel events** — do not
read them as GPU activity. Measured 133.8 W mean per APU socket (68-153 W).

## dftracer build source

PAPI and variorum require the **czgitlab** dftracer, not GitHub. See
[[tools-dftracer]] / [[feedback-never-prebuilt-when-config-knobs-needed]].
Verify `DFTRACER_PAPI_TRACING_ENABLE 1` and `DFTRACER_VARIORUM_ENABLE 1` in the
installed `dftracer_config.hpp` and `variorum power domains to build: AMD_GPU`
in the build log — a GitHub `develop` install reports success while silently
dropping both CMake options as "unused".

## Annotation is correctness-preserving — and how to prove it

miniVite is deterministic for a given `-n` and rank count, so it self-verifies.
Compare `Modularity, #Iterations` across three binaries at identical arguments:

| binary | modularity | iters |
| --- | --- | ---: |
| pristine baseline | 0.75311 | 20 |
| annotated, `DFTRACER_ENABLE=0` | 0.75311 | 20 |
| annotated, full tracing on | 0.75311 | 20 |

Run this check every session — it is cheap and it is the only thing that catches
an annotator inserting a macro somewhere that changes control flow. (For C++ the
RAII `DFTRACER_CPP_FUNCTION()` form emits no `END` macros at all, so the
"`END` inserted as the body of a braceless `if`" hazard cannot arise; that hazard
is C-mode-specific. Verify anyway with a grep for a DFTRACER line immediately
following a braceless `if`/`for`/`while`.)

Do NOT compare the `Average total time` line across these runs — it is the
Louvain phase only (~0.2-0.35 s here), far below noise, and it is not the run's
wall time. Wall time is dominated by RGG generation (see run sizing above).
