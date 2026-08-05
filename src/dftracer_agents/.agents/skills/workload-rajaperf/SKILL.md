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

## CMake configuration that worked (ROCm 7.2.1, Tuolumne)

```
-DENABLE_HIP=ON -DRAJA_PERFSUITE_ENABLE_MPI=ON -DENABLE_OPENMP=ON -DENABLE_TESTS=ON \
-DCMAKE_PREFIX_PATH=/opt/rocm-7.2.1 -DRAJA_ENABLE_EXTERNAL_ROCPRIM=ON \
-Drocprim_DIR=/opt/rocm-7.2.1/lib/cmake/rocprim
```

`-DENABLE_HIP=ON` is a BLT-provided option (not `RAJA_PERFSUITE_*`) — it is
genuine app GPU usage (real HIP kernel backend variants per benchmark), not a
false positive from a stray ROCm module.

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
