---
name: workload-kripke
description: Kripke (LLNL deterministic Sn transport proxy app, C++17 + RAJA/Umpire/CHAI, BLT/CMake superbuild) — how to build it without the ExternalProject wrapper, which of its 26 sources actually need dftracer annotation, the FINI-vs-MPI-finalize ordering trap in its comm wrapper, and why its headers must NOT be annotated. Load this for any Kripke build, annotation, or tracing session.
---

# workload-kripke

Kripke solves the discrete-ordinates (Sn) Boltzmann transport equation
(Kobayashi benchmark 3i). ~5k lines of its own C++ plus RAJA/Umpire/CHAI/BLT
submodules. Annotated/instrumented by dftracer, so it is `workload-*` here
because it is a traced scientific proxy app.

Related: [[system-tuolumne]], [[software-papi]], [[dftracer-annotate-cpp]],
[[tools-dftracer]], [[software-cmake]].

## Build

**Submodules are mandatory and there are eight of them.** `git submodule
update --init --recursive` before anything; without it CMake fails with
"The BLT submodule is not present." or "CAMP submodule not initialized".

**The top-level `CMakeLists.txt` is a superbuild, not the app.** It wraps the
real project in `ExternalProject_Add(kripke_ep ...)` purely so it can build
the bundled `adiak` first when `ENABLE_CALIPER=On`. With Caliper off (the
default) that layer buys nothing and only hides the build. **Configure the
inner project directly:**

```bash
cmake <src>/cmake/kripke \
  -DKRIPKE_SOURCE_ROOT=<src> \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo \
  -DCMAKE_C_COMPILER=$CC -DCMAKE_CXX_COMPILER=$CXX \
  -DENABLE_MPI=On -DENABLE_OPENMP=On \
  -DENABLE_TESTS=Off -DENABLE_EXAMPLES=Off \
  -DBLT_SOURCE_DIR=<src>/blt
```

`KRIPKE_SOURCE_ROOT` is required (the inner CMakeLists `FATAL_ERROR`s without
it) and is the only thing the superbuild was passing down.

Two flags worth setting that the project does not set for you: RAJA's
`-DRAJA_ENABLE_EXERCISES=Off -DRAJA_ENABLE_EXAMPLES=Off -DRAJA_ENABLE_TESTS=Off
-DRAJA_ENABLE_BENCHMARKS=Off`. Kripke's own `ENABLE_EXAMPLES=Off` does NOT
reach RAJA, so a default build spends most of its wall time compiling ~100
RAJA tutorial binaries you will never run. Also build the `kripke.exe` target
by name rather than `all`.

`KRIPKE_ARCH` is `Sequential` by default and is silently overridden by whichever
of `ENABLE_OPENMP` / `ENABLE_CUDA` / `ENABLE_HIP` is on. The banner the binary
prints ("Architecture: OpenMP") is the authoritative confirmation.

**CPU (MPI+OpenMP) vs GPU (HIP).** The CPU build is a few minutes and needs
nothing but RAJA. The HIP path additionally requires `ENABLE_CHAI=On`, which
drags in CAMP + Umpire + CHAI compiled for the GPU — a much larger and more
fragile build. Choose CPU unless GPU kernel tracing is the actual goal.

## Annotation (dftracer, C++ RAII mode)

26 `.cpp` files under `src/`. `clang_annotate_project` handles them in one
pass **only if you pass `compile_flags`** — every Kripke source includes
`KripkeConfig.h` (generated into the build dir) plus RAJA and camp headers,
and a header clang cannot find is a fatal parse error that yields
`functions: 0` with a green status:

```
-std=c++17 -I<src>/src -I<build>/include
-I<src>/tpl/raja/include -I<build>/raja/include
-I<src>/tpl/raja/tpl/camp/include -I<build>/raja/tpl/camp/include
-I<mpi>/include
```

`<build>/include` and `<build>/raja/include` are generated; reusing the
pristine baseline build's copies is fine.

**Exclude the submodules** — `["/tpl/", "/blt/", "/scripts/", "/host-configs/"]`.
Kripke's own tree is 26 files; the full `annotated/` tree is ~1900.

### The cost filter skips two functions you must add back by hand

The AST cost heuristic scores a function by its own body, so Kripke's two
most important entry points look trivial and are skipped, because each is
just a timer macro plus a dispatch:

| function | file | why it matters |
| --- | --- | --- |
| `Kripke::Kernel::sweepSubdomain` | `Kripke/Kernel/SweepSubdomain.cpp` | the hot sweep kernel — ~50% of solve time |
| `SweepComm::addSubdomain` / `workRemaining` / `readySubdomains` / `markComplete` | `Kripke/ParallelComm/SweepComm.cpp` | the entire parallel-sweep communication layer |

`Kripke::Generate::generateEnergy` is skipped for the same reason while its
five `Generate/*` siblings are annotated. Add all of these with
`clang_insert_line` (`DFTRACER_CPP_FUNCTION()` + a `comp` UPDATE: `"cpu"` for
the kernel/generate ones, `"comm"` for SweepComm).

Genuinely trivial and correctly skipped: `Core/BaseVar.cpp`, `Core/DataStore.cpp`,
`Core/MemoryManager.cpp` (ctors and getters only).

### FINI must precede `Kripke::Core::Comm::finalize()`

Kripke never calls `MPI_Finalize` directly — it is wrapped in
`Kripke::Core::Comm::finalize()` (`src/Kripke/Core/Comm.h`). The annotator's
teardown anchor and `clang_lint_annotations` rule L3 both match the literal
symbol, so both are satisfied while `DFTRACER_CPP_REGION_END` + `FINI` sit
*after* MPI teardown. Move them above the call by hand:

```cpp
  DFTRACER_CPP_REGION_END(main);
  DFTRACER_CPP_FINI();
  Kripke::Core::Comm::finalize();
```

`usage()` also calls `Comm::finalize()` and then `exit(1)` on the bad-argument
path, so it needs its own `DFTRACER_CPP_FINI()` or that exit loses the trace.
`main`'s early `exit(1)` on `vars.checkValues()` is handled correctly by the
tool.

### Do NOT annotate Kripke's headers

Unlike apps whose solver lives in headers, Kripke's does not, and its headers
are actively hostile to instrumentation:

* `Kripke/Arch/*.h` (LTimes, LPlusTimes, Scattering, Source, Population,
  SweepSubdomains) are ~2700 lines of pure RAJA *policy typedefs* — no
  executable functions at all.
* `Core/Field.h`, `Core/Set.h`, `Core/VarLayout.h`, `VarTypes.h` are
  per-element accessors called inside RAJA kernels. Instrumenting them would
  emit events per zone per direction per group.
* `Core/Comm.h` is the ONLY header with real work — eight thin `MPI_Allreduce`
  / `MPI_Scan` / `MPI_Init` / `MPI_Finalize` wrappers — and dftracer's brahma
  interception already records every one of them natively as `collective` /
  `comm` events. Annotating it adds a duplicate layer, not coverage.

## Running

Kripke decomposes space over ranks as `--procs x,y,z`, whose product must equal
the rank count, and `--zones` must divide evenly by it. It writes no data files
of its own, so the only I/O in a trace is startup (`/proc`, `/sys`, libraries)
plus stdout.

Measured shape (MI300A node, 2 nodes x 4 ranks, 23 cores/rank, `OMP_NUM_THREADS=23`):
`--zones 96,96,96 --niter 15 --procs 2,2,2` runs ~45 s and produces ~930k
events with full annotation + PAPI. Scale `niter` for longer runs; wall time is
close to linear in it.

`--zones 16,16,16 --niter 5` at one rank is a good few-second local probe.

### Event-count shape, and the one hot spot

The parallel sweep is a **polling loop**: `SweepSolver` spins on
`workRemaining()` -> `readySubdomains()` -> `testRecieves()` -> `MPI_Testany`.
At 8 ranks that alone was ~156k events per name and 159k `MPI_Testany` calls —
about 70% of the whole trace. This is real and useful signal (it is exactly
where sweep-efficiency loss shows up), but it dominates the event budget, and
it grows with rank count and with how long any rank waits. If a Kripke trace is
unexpectedly huge, this is why; drop the three `SweepComm`/`ParallelComm`
polling functions before blaming the tracer.

Instrumentation overhead measured with `DFTRACER_ENABLE=0`: ~0.6% on wall time,
with particle counts bit-identical to the pristine baseline.

## Verification checklist

* banner shows `MPI Enabled: Yes`, `OpenMP Enabled: Yes`, `Architecture: OpenMP`
* `ldd kripke.exe | grep dftracer` shows `libdftracer_core.so`
* run the annotated binary with `DFTRACER_ENABLE=0` and diff the per-iteration
  `particle count=` lines against the pristine build — they must match exactly
* in the trace: `CPP_APP` present AND `sweepSubdomain` present (if the second
  is missing, the cost-filter skip above was not repaired)
