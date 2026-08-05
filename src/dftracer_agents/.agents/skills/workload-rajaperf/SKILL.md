---
name: workload-rajaperf
description: Build/annotation caveats specific to RAJAPerf (LLNL/RAJAPerf), a CMake+BLT compute-kernel benchmark suite with HIP/MPI/OpenMP variants.
---

# workload-rajaperf

RAJAPerf (https://github.com/LLNL/RAJAPerf) is a loop/kernel-based compute
benchmark suite (60+ micro-kernels), not a data/checkpoint app — it performs
no file I/O in its core kernels. dftracer annotation should focus on the
orchestration/dispatch layer and MPI call sites, never the individual kernel
bodies.

## I/O dimension is structurally negligible at verified true 4-node/16-rank scale (2026-08-05)

Full `dftracer-io-optimization` checklist walked (all 12-13 categories:
write buffering, read caching, prefetch/fadvise, stage-in/out, ROMIO
collective buffering, HDF5, other I/O middleware, Lustre striping/PFL/DoM,
burst buffer, OS/kernel I/O path, metadata tuning, compute-I/O overlap,
file-per-process vs shared, request-size shape) — every one not-applicable.
RAJAPerf has no checkpoint/dataset I/O by design (confirmed earlier in this
skill); the small `STDIO`/`POSIX` trace bucket that does appear is ~77%
ROCm/hwloc `/proc`+`/sys` probing (`/proc/cpuinfo` reopened thousands of
times) plus console output, not application data I/O — real app-level file
activity in a full run is a handful of `read`/`pread` calls and under 25 KB
written. One bounded (<1% ceiling), not-yet-measured lever exists if I/O
optimization is ever revisited: `HWLOC_XMLFILE` to cache the hwloc topology
probe result and avoid re-walking `/proc`/`/sys` on every launch. **General
pattern: a pure compute-kernel micro-benchmark with no checkpoint/dataset
should be expected to produce a whole-checklist "not applicable" I/O
verdict — this is the correct, complete outcome, not a shortcut.**

## `MPI_Barrier` in the trace is RAJAPerf's own timing harness, not real communication cost (2026-08-05, true 4-node/16-rank scale)

At verified 4-node/16-rank scale, `MPI_Barrier` showed up as ~8.5%/rank of
wall time — a real, measurable cost, but investigate its SOURCE before
treating it as an optimization target. `grep -rn MPI_Barrier
annotated/source/src/` found the call sites inside
`KernelBase::startTimer()`/`stopTimer()` (`src/common/KernelBase.hpp`), which
bracket EVERY timed kernel-execution window across all kernel x variant x
tune x pass combinations — i.e. RAJAPerf barriers all ranks before/after each
timing measurement specifically so the reported per-kernel timing is
cross-rank-synchronized and comparable. **This is a benchmark-measurement
artifact, not algorithmic communication cost — do NOT propose reducing
barrier frequency as an optimization; that would change what the benchmark
measures, not make the workload faster.** The separate signal to actually
look at for real inter-node communication cost is the halo-exchange p2p
calls (`MPI_Waitany` on `Isend`/`Irecv`, ~2.7%/rank) inside the
`Comm_HALO_PACKING`/`Comm_HALO_EXCHANGE` kernels specifically — those ARE
legitimate algorithmic communication. General pattern for any MPI
micro-benchmark suite: when a barrier/collective shows up as a bottleneck,
check whether it's inside the suite's OWN timing harness before assuming
it's part of the measured workload.

## Diagnosed as GPU-launch-overhead-bound by design (2026-08-05, Tuolumne 4-node/16-rank baseline)

A verified-valid baseline trace (12 kernels x Base_HIP x 3 passes, ~1.24M
events) showed host-side `HIP_RUNTIME_API` (22.76s) far exceeding actual
`KERNEL_DISPATCH` compute time (4.05s) — ~9.4us/kernel execution vs.
~43.4us/host HIP_RUNTIME_API call. Root cause: RAJAPerf's internal
`RepCount` auto-scaling issues thousands of kernel-launch repetitions per
(kernel,pass) to get a statistically stable timing measurement on
deliberately tiny micro-benchmark kernels — this is inherent to how
RAJAPerf measures itself, not a misconfiguration to "fix" in the benchmark
invocation. `hipLaunchKernel`+`hipStreamSynchronize`+`hipDeviceSynchronize`
alone account for 9.23s of the 22.76s host bucket — the classic
many-small-kernel-launches regime (candidates: launch batching/fusion, HIP
graphs, eliminating redundant per-launch syncs).

Second-order finding: `KernelBase::setUp`/`tearDown` allocate device +
pinned-host (`hipHostMalloc`) arrays freshly once per (kernel,variant) —
not pooled/reused — costing 9.16s total (`hipHostMalloc` alone is 66% of
that). Worth questioning whether pinned host memory is even needed on
MI300A's unified-memory APU (see software-rocm skill's "MI300A unified
memory is intra-package only" section for the relevant memory model
caveat) rather than assuming discrete-GPU pinned-transfer conventions
apply as-is.

Communication (MPI total <11ms) and I/O (STDIO+POSIX, console output only,
0.98s) are both negligible for this kernel/variant/pass selection — expected
given the workload's nature, but confirmed rather than assumed.

## `--kernels` with an unrecognized name silently runs ZERO kernels — no error (2026-08-05)

RAJAPerf's `--kernels <names>` matches against `<Group>_<Name>` (see
`--print-kernels` for the ground-truth list, grouped: `Basic_*`, `Lcals_*`,
`Polybench_*`, `Stream_*`, `Apps_*`, `Algorithm_*`, `Comm_*`) but ALSO
accepts an unprefixed short name when unambiguous (confirmed:
`--kernels DAXPY MULADDSUB --variants Base_Seq Base_HIP` works and produces
real `KERNEL_DISPATCH` events — these match `Basic_DAXPY`/`Basic_MULADDSUB`).
**A name that doesn't match anything (wrong short name, or a name that
doesn't exist at all) is silently DROPPED — RAJAPerf does not error, warn
clearly, or fall back to a default kernel set; it runs `Executor::runSuite()`
as a near-instant no-op (microsecond-scale span, zero `KERNEL_DISPATCH`
events, empty output CSVs in `--outdir`).** Confirmed root cause of a wasted
4-node/16-rank/~9-minute baseline trace run: `--kernels DAXPY DOT AXPY
MULADDSUB` (dropping the also-required `--variants` flag "to avoid bad input
errors") silently matched zero kernels because `DOT` alone is ambiguous/not
a registered short name (the real kernel is `Stream_DOT`) and plain `AXPY`
does not exist as any kernel (only `Basic_DAXPY` does).

**Full valid kernel list** (`--print-kernels`, this build): `Basic_ARRAY_OF_PTRS,
Basic_COPY8, Basic_DAXPY, Basic_DAXPY_ATOMIC, Basic_EMPTY, Basic_IF_QUAD,
Basic_INDEXLIST, Basic_INDEXLIST_3LOOP, Basic_INIT3, Basic_INIT_VIEW1D,
Basic_INIT_VIEW1D_OFFSET, Basic_MAT_MAT_SHARED, Basic_MULADDSUB,
Basic_NESTED_INIT, Basic_PI_ATOMIC, Basic_PI_REDUCE, Basic_REDUCE3_INT,
Basic_REDUCE_STRUCT, Basic_TRAP_INT, Basic_MULTI_REDUCE, Lcals_DIFF_PREDICT,
Lcals_EOS, Lcals_FIRST_DIFF, Lcals_FIRST_MIN, Lcals_FIRST_SUM,
Lcals_GEN_LIN_RECUR, Lcals_HYDRO_1D, Lcals_HYDRO_2D, Lcals_INT_PREDICT,
Lcals_PLANCKIAN, Lcals_TRIDIAG_ELIM, Polybench_2MM, Polybench_3MM,
Polybench_ADI, Polybench_ATAX, Polybench_FDTD_2D, Polybench_FLOYD_WARSHALL,
Polybench_GEMM, Polybench_GEMVER, Polybench_GESUMMV, Polybench_HEAT_3D,
Polybench_JACOBI_1D, Polybench_JACOBI_2D, Polybench_MVT, Stream_ADD,
Stream_COPY, Stream_DOT, Stream_MUL, Stream_TRIAD, Apps_CONVECTION3DPA,
Apps_DEL_DOT_VEC_2D, Apps_DIFFUSION3DPA, Apps_EDGE3D, Apps_ENERGY,
Apps_FEMSWEEP, Apps_FIR, Apps_INTSC_HEXHEX, Apps_INTSC_HEXRECT, Apps_LTIMES,
Apps_LTIMES_NOVIEW, Apps_MASS3DEA, Apps_MASS3DPA, Apps_MASS3DPA_ATOMIC,
Apps_MASSVEC3DPA, Apps_MATVEC_3D_STENCIL, Apps_NODAL_ACCUMULATION_3D,
Apps_PRESSURE, Apps_VOL3D, Apps_ZONAL_ACCUMULATION_3D, Algorithm_SCAN,
Algorithm_SORT, Algorithm_SORTPAIRS, Algorithm_REDUCE_SUM,
Algorithm_MEMSET, Algorithm_MEMCPY, Algorithm_ATOMIC, Algorithm_HISTOGRAM,
Comm_HALO_PACKING, Comm_HALO_PACKING_FUSED, Comm_HALO_SENDRECV,
Comm_HALO_EXCHANGE, Comm_HALO_EXCHANGE_FUSED` — the `Comm_*` group is the
one with genuine multi-rank MPI communication (halo exchange), useful when a
baseline run specifically wants a real communication signal, not just
compute.

**Full valid variant list** (`--print-variants`): `Base_Seq, Lambda_Seq,
RAJA_Seq, Base_OpenMP, Lambda_OpenMP, RAJA_OpenMP, Base_OpenMPTarget,
RAJA_OpenMPTarget, Base_CUDA, Lambda_CUDA, RAJA_CUDA, Base_HIP, Lambda_HIP,
RAJA_HIP, Kokkos_Lambda, Base_SYCL, RAJA_SYCL`.

**Rule for any future run of this app:** ALWAYS use the full `<Group>_<Name>`
form for `--kernels` (never a bare short name, even one that happens to
work) to avoid ambiguity, and ALWAYS verify the run actually executed
before trusting it — check `runSuite()`'s span duration is not
microsecond-scale, check `KERNEL_DISPATCH`/`HIP_RUNTIME_API` event counts
are non-zero in the resulting trace, and check `--outdir` actually contains
the expected `<outfile>-timing-<combiner>.csv` files. A `raja-perf.exe`
that exits 0 with no error text is NOT sufficient evidence the intended
kernels ran.

## CMake configuration that worked (ROCm 7.2.1, Tuolumne)

```
-DENABLE_HIP=ON -DRAJA_PERFSUITE_ENABLE_MPI=ON -DENABLE_OPENMP=ON -DENABLE_TESTS=ON \
-DCMAKE_PREFIX_PATH=/opt/rocm-7.2.1 -DRAJA_ENABLE_EXTERNAL_ROCPRIM=ON \
-Drocprim_DIR=/opt/rocm-7.2.1/lib/cmake/rocprim
```

`-DENABLE_HIP=ON` is a BLT-provided option (not `RAJA_PERFSUITE_*`) — it is
genuine app GPU usage (real HIP kernel backend variants per benchmark), not a
false positive from a stray ROCm module.

**`-DRAJA_PERFSUITE_ENABLE_MPI=ON` alone is not enough — also pass
`-DENABLE_MPI=ON`.** `RAJA_PERFSUITE_ENABLE_MPI` is a
`cmake_dependent_option` gated on BLT's own `ENABLE_MPI` switch; without the
latter also set, MPI stays silently OFF (confirmed via
`RAJA_PERFSUITE_NUM_MPI_TASKS: 0` in the configure output) even though the
RAJAPerf-level flag was passed and cmake reports success. Always pass BOTH.

## Linking dftracer into RAJAPerf's own build

RAJAPerf's CMakeLists.txt does not auto-discover dftracer. Add, right after
`set(RAJA_PERFSUITE_DEPENDS RAJA)` in the top-level `CMakeLists.txt`:
```cmake
find_package(rocprofiler-sdk CONFIG REQUIRED)
find_package(dftracer REQUIRED)
list(APPEND RAJA_PERFSUITE_DEPENDS dftracer)
```
Two things that are easy to get wrong here:
- dftracer's installed `dftracer-targets.cmake` link interface references
  `rocprofiler-sdk::rocprofiler-sdk` but never resolves that dependency
  itself — the CONSUMER (RAJAPerf) must `find_package(rocprofiler-sdk)`
  itself, BEFORE `find_package(dftracer)`, or the dftracer find_package
  fails with an unresolved target error.
- The correct link target is the plain `dftracer` target (a backward-compat
  alias) — NOT `dftracer::dftracer`, which does not exist for this build.
- Pass `-DCMAKE_PREFIX_PATH` as a `;`- or `:`-separated list containing BOTH
  `/opt/rocm-<ver>` (for rocprofiler-sdk) AND the dftracer install prefix
  (`<venv>/lib/python3.13/site-packages/dftracer`) — CMake searches every
  entry, order doesn't matter.

## Real annotation-tool corruption bug hit and fixed at the tool level (2026-08-05)

`clang_add_braces`/`add_braces_c` corrupted `src/common/Executor.cpp` and
`src/common/KernelBase.cpp` with a dozen+ stray brace pairs at locations with
NO if/for/while at all (a constructor member-initializer list, a `switch`
`case` label, random unrelated statement pairs) — caused by clang parsing
these files with NO include paths for RAJA/BLT/camp's nested submodule
headers, silently degrading to error-recovery mode and reporting bogus AST
line ranges. Fixed at the tool level (see `dftracer-annotation-lessons`
LESSONS_LOG.md 2026-08-05 entry for the full root-cause/fix writeup) — the
tool now refuses to touch a file when clang can't parse it cleanly, rather
than trusting a degraded AST. **Because RAJA/BLT's submodule include tree is
still not something the tool auto-discovers, brace insertion will safely
SKIP (not corrupt) on RAJAPerf source files** — check
`clang_annotate_file`'s response for a `braces_skipped_reason` and manually
verify (grep for bare `if (...)`/`for (...)`/`while (...)` without a
following `{`) whether the specific function you're annotating has any real
braceless control-flow body that needs its END macro placed correctly by
hand.

## `main()`'s REGION_START/END macros must use a bare identifier, not a quoted string (2026-08-05)

`DFTRACER_CPP_REGION_START("main")` (with quotes) fails at final link with
`pasting formed 'profiler_"main"', an invalid preprocessing token` —
dftracer.h token-pastes `profiler_##name`, which requires a bare identifier.
Correct form: `DFTRACER_CPP_REGION_START(main);` /
`DFTRACER_CPP_REGION_END(main);` (no quotes — the event name is derived
internally via `#name` stringization). The paired UPDATE call must be
`DFTRACER_CPP_REGION_DYN_UPDATE(main, "comp", "cpu");`, NOT
`DFTRACER_CPP_FUNCTION_UPDATE(...)` (that one targets `profiler_dft_fn`,
which only the RAII `DFTRACER_CPP_FUNCTION()` macro declares — REGION_START
declares `profiler_main` instead, needing the arrow-deref DYN_UPDATE
variant). This was invisible until the FINAL LINK of the full ~600-file
build (`clang_lint_annotations` doesn't check macro-argument correctness
against the real header) — see `dftracer-annotation-lessons` LESSONS_LOG.md
2026-08-05 entry for the full writeup and the `clang_annotate_file` tool fix.

## Driver / dispatch file locations (annotation scope)

- `src/RAJAPerfSuiteDriver.cpp` — `main()`. Only entry point; owns
  `MPI_Init`/`MPI_Finalize` and constructs the `Executor`.
- `src/common/Executor.cpp` — `Executor::runSuite()` (top-level pass/kernel
  loop, once-per-pass granularity) and `Executor::runKernel()` (once-per-
  kernel-variant-tuning dispatch; also the site of `MPI_Comm_size` and the
  `Allreduce` checksum-tolerance reduction across ranks — annotate as
  `comp="comm"`).
- `src/common/KernelBase.cpp` — `KernelBase::execute()` (once-per-invocation:
  wraps setUp/runKernel/tearDown + checksum bookkeeping) and
  `KernelBase::runKernel()` (the actual dispatch: `(this->*(variant_tuning_methods[vid].at(tune_idx)))(vid)`
  — a function-pointer call into each kernel's `runHipVariant()`/
  `runOpenMPVariant()`/etc. This IS the shared GPU kernel-dispatch entry
  point mentioned in the pipeline plan; annotate it once here rather than
  inside every one of the 60+ individual kernel files' `runXVariant()` bodies).
- **No shared HIP-launch helper exists under `src/common/`** — confirmed by
  `grep -rn "runHipVariant" src/common/*.cpp src/common/*.hpp` returning
  nothing. Each kernel file implements its own `runHipVariant()`. Do not
  search for one again in a future session; annotate only at the
  `KernelBase::runKernel()` function-pointer call site instead.
- Individual kernel files under `src/<Category>/*.cpp` (basic/, lcals/,
  polybench/, stream/, apps/, comm/, algorithm/) are intentionally NOT
  annotated at any granularity — both their CPU/OpenMP variant bodies and
  their `runHipVariant()` implementations. Annotating any of these would put
  dftracer macros inside a tight per-kernel-iteration hot loop (same hazard
  class as CP10 in dftracer-annotation-lessons).

## DFTRACER_CPP_INIT-before-HIP-runtime-call ordering (C++ analogue of the Python "import torch before initialize_log()" rule)

For a natively HIP-linked C++ binary (not dlopen'd), `libamdhip64.so` is
loaded by the dynamic linker at **process start**, before `main()` runs —
there is no equivalent "import" statement to order against. The correct
placement is therefore: `DFTRACER_CPP_INIT()` as the **very first statement
inside `main()`**, before `MPI_Init()` and before the `Executor` is
constructed (the `Executor` is the first thing that can touch the GPU, e.g.
via RAJA resource/HIP stream setup). Do not place it inside an
`#if defined(RAJA_PERFSUITE_ENABLE_MPI)` block after `MPI_Init()` — besides
being late, it would silently vanish from a non-MPI build.

## Real annotation tool bug hit this session (2026-08-05)

`clang_annotate_file` with both `exclude_functions` (an exclude-list, used to
skip trivial/report-writing functions) and `comp_overrides` set silently
annotated **zero** target functions in `src/common/Executor.cpp` and
`src/common/KernelBase.cpp` — it inserted only the
`#include <dftracer/dftracer.h>` line, reported `"functions": 0`, and then a
second call reported `"already_annotated": true` (because the include alone
was treated as proof of prior annotation), permanently masking the gap. Grep
verification (`grep -n DFTRACER_CPP <file>`) after the first call is what
caught this — it must always be done per Rule G, not just trusted from the
tool's own `written_to_disk`/insertion-count response, when `exclude_functions`
is combined with `comp_overrides` in the same call.
Fix applied: hand-inserted `DFTRACER_CPP_FUNCTION(); DFTRACER_CPP_FUNCTION_UPDATE("comp", ...);`
directly after the opening brace of each target function (`Executor::runSuite`,
`Executor::runKernel`, `KernelBase::execute`, `KernelBase::runKernel`) via a
targeted Python string-replace, then re-ran `clang_lint_annotations` (passed
clean) to confirm ordering correctness.

Separately, for the `main()` entry-point file (`is_entry=True`), the same tool
call inserted `DFTRACER_C_INIT(...)` (the **C**, not C++, init macro — see
dftracer-annotation-lessons CP1) placed *inside* the
`#if defined(RAJA_PERFSUITE_ENABLE_MPI)` block after `MPI_Init()`, plus a bare
`DFTRACER_CPP_FUNCTION()` in `main()` (violates CP2 — RAII in `main()` fires
after `DFTRACER_CPP_FINI()`), and added no `DFTRACER_CPP_FINI()` at all. All
three defects were hand-fixed: `DFTRACER_CPP_INIT()` moved unconditionally to
the first line of `main()`, `DFTRACER_CPP_FUNCTION()` replaced with
`DFTRACER_CPP_REGION_START("main")`/`DFTRACER_CPP_REGION_END("main")` per CP2/CP3,
and `DFTRACER_CPP_FINI()` added immediately before `return 0;`.

## `session_annotation_report` false-negative on RAII-style C++ macros

After the manual fix above, `clang_lint_annotations` passed clean on all 3
files and `grep -c DFTRACER_CPP` confirmed the macros were present on disk,
but `session_annotation_report` still reported `0/37 functions annotated
(0.0% coverage)` for the same files. Its detection regex appears to look for
the C-style `DFTRACER_C_FUNCTION_START` pattern (or an `annotation_status.md`
entry) rather than the C++ RAII `DFTRACER_CPP_FUNCTION()` call — every
function it listed as `"not_annotated"` included ones we had NOT scoped in
(e.g. `writeFOMReport`, `print`) as well as the ones we deliberately did
annotate (`runSuite`, `runKernel`, `execute`, `main`), so the 0% figure is not
usable as a coverage signal for a C++ file annotated via the RAII macro
family. Ground-truth C++ coverage with `grep -n "DFTRACER_CPP_FUNCTION\|DFTRACER_CPP_REGION\|DFTRACER_CPP_INIT\|DFTRACER_CPP_FINI"` on the annotated
file instead until this is fixed at the tool level.

## `clang_syntax_check` include-path gap on RAJAPerf/BLT

`clang_syntax_check` (standalone `mpicxx -fsyntax-only`, no CMake) fails on
every RAJAPerf source file with `fatal error: 'RAJA/config.hpp' file not
found` / `'common/RAJAPerfSuite.hpp' file not found` — it only passes
`-I<dir-of-file>` and one build dir, not the full RAJA/BLT/camp submodule
include tree or the CMake-generated `RAJA/config.hpp`. This is an
environment/include-path limitation of the standalone syntax-check tool, not
a defect in the annotation — defer real compile validation to the STEP 3
(`dftracer-build-smoke`) full CMake build, which has the correct include
paths, rather than treating this failure as an annotation error.

## Base_HIP allocation cost is a pinned-STAGING artefact, not device allocation (2026-08-05)

Symptom: `hipHostMalloc` dominates the HIP runtime bucket in any `Base_HIP` run
(measured 988 calls / 6.05 s, ~6.1 ms *per call*, 66% of a 9.16 s alloc/free
bucket on a 4-rank run).

Root cause (source chain, all in the upstream tree):
`RunParams.hpp` defaults `hipDataSpace = DataSpace::HipDevice` ->
`DataUtils.cpp::hostCopyDataSpace(HipDevice)` returns `HipPinned` ->
`DataUtils.hpp::allocAndInitData()` allocates in the *hostCopy* space first,
initialises on the host, then `moveData()`s to the target space. So EVERY device
array in EVERY `KernelBase::setUp()` does hipHostMalloc + H2D copy + hipHostFree.
It is NOT the halo/MPI pinned buffers (those are only ~2 allocs per Comm kernel).

Exact fix (CLI only, no rebuild, no source patch):
`raja-perf.exe ... --hip-data-space HipManaged`. `hostCopyDataSpace(HipManaged)`
returns HipManaged itself, so the staging allocation AND the staging copy both
disappear. Measured: hipHostMalloc 988/6.05 s -> 48/0.055 s (the 48 residual are
the HipPinned MPI halo buffers), hipMalloc 870/2.335 s -> 32/0.002 s, replaced by
hipMallocManaged 1708/1.909 s; per-rank alloc bucket 2.29 s -> 0.726 s (-68%).
Checksums all PASSED (only the atomic-reduction tunings differ at 1e-16, which is
GPU atomic-order nondeterminism present run-to-run within one config too).

Caveats:
- On MI300A the staging copy is architecturally redundant (CPU cores and GPU XCDs
  share the same in-package HBM) — refs arXiv 2405.00436, arXiv 2508.12743.
- Do NOT try `--hip-data-space HipHostAdviseFine` as the "even purer APU" variant:
  it is already recorded in the optimization KB as a hard SIGABRT ("Memory access
  fault by GPU node-4") on ROCm 7.2.1 — plain malloc'd host memory is not
  GPU-addressable and `hipMemAdvise` does not register the range.
- Wall-time ceiling is small: setUp runs once per (kernel,variant) while the timed
  loop issues >100k kernel launches, so the whole bucket is only ~3% of wall time.
  Expect this to be UNRESOLVABLE against normal run-to-run noise; validate it on the
  trace-level allocation bucket, not on wall time.

## Compute-optimization lessons (2026-08-05, 4-node/16-rank MI300A, dftracer STEP 7)

- **Symptom:** trace shows `HIP_RUNTIME_API` 22.8 s + `KERNEL_DISPATCH` 4.0 s and the two
  `Comm_HALO_*` `Base_HIP` kernels look 10-18x SLOWER than `Base_Seq`.
  **Root cause:** observer effect. dftracer/rocprofiler HIP interception inflates exactly
  the kernels that issue the most HIP calls. **Exact fix:** read RAJAPerf's OWN
  `<outdir>/RAJAPerf-timing-Average.csv` / `RAJAPerf-kernel-run-data.csv` instead of sizing
  anything from the trace bucket. Untraced p50 (5 reps): `Comm_HALO_PACKING` Base_HIP
  0.144 s (not 0.960 s), `Comm_HALO_EXCHANGE` 0.185 s (not 1.121 s) - and untraced,
  HALO_EXCHANGE Base_HIP is FASTER than Base_Seq. RAJAPerf always writes this report; use it.
- **`KernelBase`/`Executor` contain NO redundant per-repetition synchronization.**
  `KernelBase::startTimer`/`stopTimer` (KernelBase.hpp) call `synchronize()` ->
  `hipDeviceSynchronize` exactly ONCE per timed window; every normal kernel's rep loop is
  pure async `RPlaunchHipKernel` enqueue. Do not spend a session looking for launch
  batching there - it is already optimal.
- **The ONE real per-rep sync pathology is `src/comm/HALO_PACKING-Hip.cpp`** (and the same
  shape in `HALO_EXCHANGE-Hip.cpp`): the `Base_HIP` pack loop calls
  `hipStreamSynchronize(res.get_stream())` once per neighbor (26/rep), while the
  structurally identical unpack loop syncs once per rep. Correctness-preserving fix is
  per-neighbor `hipEvent`s or per-neighbor streams (NOT deleting the sync - it emulates the
  buffer-ready-before-send dependency the Comm group is designed to measure).
- **`RAJA_PERFSUITE_GPU_BLOCKSIZES=""` compiles exactly ONE tuning** (`block_256`; plus
  `blkatm_direct_256`/`blkatm_occgs_256` for `Stream_DOT`). Passing `--gpu_block_size` at
  runtime SKIPS kernels that do not support the option (`RunParams.cpp` help text), which
  changes the amount of work executed - never use it as an A/B lever without a rebuild.
- **`-O3` (`CMAKE_BUILD_TYPE=Release`) vs `-O2 -g` (`RelWithDebInfo`) is a NO-OP here.**
  5 interleaved replicates: whole suite +1.00%, Base_Seq +1.08%, Base_HIP -1.24%. Only weak
  signal is sign-consistency (9/11 Base_HIP entries negative, ~1%), below noise.
- **Base_Seq kernels are very noisy** on a shared allocation: per-kernel CV 5-15% over 5
  replicates (Stream_TRIAD 15%). Since Base_Seq is ~95% of app-reported time, the
  whole-suite noise band is ~+/-5%. Budget >=5 replicates and interleave the arms.

## 2026-08-05: the compute dimension is structurally near-unoptimizable for a RAJAPerf suite run

Symptom: diagnosis reports "compute DOMINANT" (26.8 s HIP bucket, HIP_RUNTIME_API 3.1x-5.7x
KERNEL_DISPATCH). Root cause: two compounding effects, neither an optimizable app property.
Exact fix: size compute against the UNTRACED app timer and stop.

1. **Tracer inflation** - rocprofiler interception scales with call count and RAJAPerf's
   RepCount auto-scaling issues ~12k launches per (kernel,pass) window, so the HIP bucket is
   5.72x inflated (up to 9.6x on `Comm_HALO_*`).
2. **The remaining GPU work is tiny.** Untraced, Base_HIP is **2.1%** of total timed work;
   Base_Seq - RAJAPerf's SERIAL REFERENCE variant, unoptimizable by definition - is 97.9%.
   So the hard compute ceiling for a `--variants Base_Seq Base_HIP` run is ~2%.
3. **Most compute levers are forbidden here, not merely low-value.** RAJAPerf's kernels ARE
   the specimen under test: kernel fusion across RepCount, vendor-library kernel swaps,
   mixed precision, and reducing the per-kernel `MPI_Barrier` in
   `KernelBase::startTimer/stopTimer` all change WHAT is measured, i.e. forbidden
   "do-less"/pattern-swap moves, not optimizations.
4. **Measured no-ops here (do not re-run):** `CMAKE_BUILD_TYPE=Release`/-O3 (+1.0% suite),
   `RAJA_PERFSUITE_TUNING_HIP_ARCH=942` (0.0%), `HSA_ENABLE_INTERRUPT=0` (**+3.78%
   REGRESSION** - busy-poll steals host cores from the dominant Base_Seq kernels).
5. **Only remaining physically-valid compute lever:** HIP graphs / `hipGraphLaunch` capture of
   the per-repetition launch loop (Ekelund et al., https://arxiv.org/pdf/2501.09398v1) - a
   runtime dispatch change that preserves per-repetition semantics. Ceiling <=2.1%, realistic
   <1%, and it requires a RAJA/RAJAPerf source change. **Verdict: not worth an allocation slot
   unless the run selects GPU-only variants (drop `Base_Seq`), which raises the ceiling from
   ~2% to ~100% of timed work and is the prerequisite for any future compute pass.**

## Communication dimension (2026-08-05, 4 nodes / 16 ranks, MI300A, Cray MPICH)

Symptom: at TRUE multi-node scale the diagnoser flags `MPI_Barrier` ~8.5%/rank and
`MPI_Waitany` ~2.7%/rank. (An earlier effectively-single-process run wrongly reported
communication as "<11 ms, negligible" — never rank RAJAPerf's communication from a run whose
rank count was not independently verified.)

- **`MPI_Barrier` is RAJAPerf's own timing harness, not algorithmic traffic.** Exactly 4 call
  sites exist: `src/common/KernelBase.hpp` `startTimer()`/`stopTimer()` (a
  `MPI_Barrier(MPI_COMM_WORLD)` before AND after every timed kernel window, guarded by
  `RAJA_PERFSUITE_ENABLE_MPI`), plus `src/common/OutputUtils.cpp` twice (once per suite,
  negligible). **None are inside `Comm_HALO_*` kernel bodies.** Its magnitude scales with
  (#kernels x #variants x #tunes x npasses), i.e. with kernel-switch cadence, not with
  compute or halo volume. Reducing/removing it is FORBIDDEN — it changes what RAJAPerf
  measures. The only legitimate lever is MPI collective-algorithm selection via env var.
- **The real algorithmic communication is the halo p2p.** `Comm_HALO_EXCHANGE` /
  `Comm_HALO_PACKING` post `MPI_Irecv` for all neighbors, then per neighbor: pack kernels ->
  optional `hipMemcpyAsync` -> **`hipStreamSynchronize` -> `MPI_Isend`** (confirmed
  `src/comm/HALO_EXCHANGE-Hip.cpp:95`), then `MPI_Waitany` unpack loop and a final
  `MPI_Waitall`. The per-neighbor `hipStreamSynchronize` before each `Isend` is REQUIRED for
  correctness (the send buffer must be ready) and is part of the benchmark's specimen — do
  not remove it. RAJAPerf already ships `HALO_EXCHANGE_FUSED`, which fuses the pack kernels
  and syncs once; that is a DIFFERENT benchmark kernel, valid as a comparison point, NOT an
  "optimization" to apply to `HALO_EXCHANGE`.
- **Configuration lever that is legitimate (no source edit, no semantic change):**
  `--hip-mpi-data-space <space>` (`RunParams.hpp:494`, default `HipPinned`). `separate_buffers`
  is `(getMPIDataSpace(vid) == DataSpace::Copy)` (`src/comm/HALO_base.cpp:326`), so at the
  default there is NO staging memcpy — but pack kernels write into pinned HOST memory. Trying
  `--hip-mpi-data-space HipDevice` with `MPICH_GPU_SUPPORT_ENABLED=1` keeps message count,
  volume and topology identical and only changes where the MPI buffers live. On MI300A (APU,
  unified HBM) expect a small effect; on a discrete GPU it would matter more.
- Ceiling honesty: the two buckets bound the whole communication dimension at ~11% of a
  TRACED run's per-rank time, and ~8.5 of those 11 points are harness barrier that only an
  env var may touch. Do not budget a large allocation slot for this dimension.

## Memory dimension on MI300A: `--hip-data-space HipManaged` is a REGRESSION (measured 2026-08-05)

- **Symptom -> root cause -> verdict.** RAJAPerf's default `--hip-data-space HipDevice` makes
  `hostCopyDataSpace(HipDevice) == HipPinned` (`DataUtils.cpp`), so every device array in every
  `KernelBase::setUp()` does a `hipHostMalloc` + host init + `hipMemcpy` H2D + `hipHostFree`.
  On an APU with shared in-package HBM the pinned staging buffer looks architecturally
  pointless, and `--hip-data-space HipManaged` does eliminate it (measured -68% on the HIP
  alloc bucket). **But at true 4-node/16-rank scale, untraced, 5 interleaved replicates, it is
  a consistent REGRESSION: wall 75.61 s -> 77.81 s (-2.9%), and RAJAPerf's own
  `RAJAPerf-timing-Average.csv` timed-kernel sum 21.59 s -> 22.35 s (-3.6%). Paired
  per-replicate delta positive 5/5.** The larger delta on the TIMED sum proves the penalty
  lands inside the timed kernel region, not in setup: `hipMallocManaged` memory on MI300A is
  fine-grained/coherent while `hipMalloc` device memory is coarse-grained, and fine-grained
  accesses cost more per access in GPU-resident streaming kernels (arXiv 2508.12743).
  **Do not adopt `HipManaged`.** The setUp staging saving is one-time; the coherence penalty
  is per-access, and RAJAPerf's timed loop dominates.
- **Do not retry `--hip-data-space HipHostAdviseFine`** — hard SIGABRT rc=134, "Memory access
  fault by GPU node-N" on the first kernel under ROCm 7.2.1. `hipMemAdvise` gives placement
  hints but does not register/map plain `malloc`'d memory; unified *physical* memory on MI300A
  does not make an arbitrary host pointer GPU-addressable.
- **Roofline gate for the whole memory dimension (reuse this before spending allocation
  time).** At the default problem size of 1,000,000 elements (~8 MB per array) the working set
  is resident in MI300A's 256 MB Infinity Cache, and `RAJAPerf-kernel-run-data.csv` shows
  Base_HIP streaming kernels already at 2272-2850 GiB/s (~45-55% of HBM3 peak). RAJAPerf at
  default sizes is therefore **cache-resident and launch-overhead-bound, NOT HBM-bandwidth-
  bound** — cache blocking, huge pages/TLB, NUMA membind and allocator-arena tuning all have
  no pressure to relieve. Base_Seq's 18-108 GiB/s is a single-threaded CPU *reference* variant,
  slow by design and not a legitimate target.
- **Scale-verification recipe (a 4-rank run silently masquerades as 4-node).** A prior pass
  measured this same flag at `flux run -N4 -n4` and wrongly concluded "no wall-time change".
  Always launch `flux run -N 4 -n 16 --exclusive` and verify all three: 16 distinct PIDs, 4
  distinct hostnames, and RAJAPerf's own `Checksum Report for 16 MPI ranks`. Also verify the
  flag took effect by grepping the run log for the `Hip - HipManaged` / `Hip - HipDevice`
  data-space echo — RAJAPerf prints it, so a silently-ignored flag is detectable for free.
- **Never run two timing drivers concurrently on one allocation.** A prior pass inflated
  byte-identical work from 71 s to 147 s to 304 s that way and produced an uninterpretable
  A/B. Serialize; interleave arms within the serial sequence.
