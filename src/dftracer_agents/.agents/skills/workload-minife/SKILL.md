---
name: workload-minife
description: miniFE (Mantevo unstructured implicit FE proxy app) specific knowledge — the five build variants, the OpenMP-4.5 GPU-offload toolchain on Tuolumne, template-header annotation strategy, run sizing, and the verify_solution trap. Load this skill whenever working with miniFE.
---

# workload-minife

miniFE is Mantevo's unstructured implicit finite-element proxy app: build a box mesh,
assemble a CSR matrix, solve with Conjugate Gradients. Small (~8k lines) but it exercises
FE assembly, sparse matvec, MPI halo exchange, and collectives — a good dftracer target.

Upstream: `github.com/Mantevo/miniFE`. The ice4hpc Tuolumne recipe pins commit **`abe3288`**
(`llnl/ice4hpc_data: apps_per_machine/tuolumne/miniFE/README.txt`).

Related: [[system-tuolumne]], [[software-rocm]], [[dftracer-annotation-lessons]],
[[bug-clang-annotator-silent-zero-functions]].

## Pick the variant FIRST — there are five, and they are not interchangeable

| Variant | What it is | GPU? |
| --- | --- | --- |
| `ref/` | serial/MPI reference | no |
| `openmp/`, `openmp-opt/` | OpenMP CPU threading | no |
| `openmp45/` | OpenMP **4.5 target offload** | yes |
| `openmp45-opt/` | tuned target-offload variant | yes |

For any HIP/GPU tracing work use `openmp45-opt`. It reaches the GPU through OpenMP target
offload, **not** through HIP source calls — which matters twice:

1. `session_detect` reports `hip: false` / `hip_tracing_needed: false`, because it only
   greps the app's own source for HIP tokens. That is a **false negative** of exactly the
   library-abstraction kind `session_install_dftracer`'s `hip` override exists for. Pass
   `hip=True` explicitly or you get no GPU events.
2. dftracer's rocprofiler-sdk interception works here — but that is *not* special to
   OpenMP offload. It is the `rocprofiler_configure` registration fix (see
   [[system-tuolumne]], "force_configure cannot win the registration race"); before that
   fix nothing GPU-side was captured on any app. Do not read a working miniFE GPU trace
   as evidence about an app's offload model.

Which HIP kinds you should expect **does** depend on the offload model. miniFE goes
through the OpenMP target runtime, so the app makes no HIP runtime calls of its own:
`KERNEL_DISPATCH`, `MEMORY_COPY` and `PAGE_MIGRATION` populate, while `HIP_RUNTIME_API`
and `SCRATCH_MEMORY` stay empty. An MFEM/RAJA/Kokkos app that calls HIP directly (laghos)
fills `HIP_RUNTIME_API` too. Absent `HIP_RUNTIME_API` on miniFE is correct, not a
regression.

## Build (Tuolumne / MI300A)

The ice4hpc recipe does NOT use the site-default PrgEnv-cray. OpenMP 4.5 offload to
gfx942 needs the AMD clang offload toolchain:

```bash
module load rocm/6.4.2
module load rocmcc/6.4.2-cce-20.0.0-magic
module load cray-mpich/9.0.1
module load papi/7.2.0.2      # LAST, on its own line -- see system-tuolumne
export CC=.../cray-mpich-9.0.1-rocmcc-6.4.2-cce-20.0.0-magic/bin/mpiamdclang
export CXX=.../cray-mpich-9.0.1-rocmcc-6.4.2-cce-20.0.0-magic/bin/mpiamdclang++
```

Use the ice4hpc `Makefile-openmp45-opt` as `openmp45-opt/src/Makefile`. Then:

**`openmp45-opt/` ships only `src/`.** It builds against the sibling variant's support
directories, which you must symlink in yourself before the first build:

```bash
cd openmp45-opt
ln -sfn ../openmp45/basic basic
ln -sfn ../openmp45/fem   fem
ln -sfn ../openmp45/utils utils
```

`make_targets` has `vpath %.cpp ../utils`, so `BoxPartition.o`/`mytimer.o`/`param_utils.o`/
`utils.o` come from `openmp45/utils/` through that symlink. Annotating "the miniFE source"
therefore means `openmp45-opt/src/*` **plus** `openmp45/utils/*.cpp`.

### `make clean` deletes a checked-in file

`make clean` runs `rm -f *.o *.a *.x *.linkinfo miniFE_info.hpp` — but **`miniFE_info.hpp`
is checked into git** in `openmp45-opt/src/`. A clean before copying to `annotated/` leaves
a tree that clang cannot parse (`fatal error: 'miniFE_info.hpp' file not found`) and that
`make` only repairs because `generate_info` regenerates it. Restore it with
`git checkout -- openmp45-opt/src/miniFE_info.hpp` before any annotation pass.

## Annotation: the work is all in templated headers

miniFE puts essentially every interesting function in a template inside a `.hpp`. Those
headers do not parse standalone, so `clang_extract_functions` / `clang_annotate_file`
return nothing useful for them — only `main.cpp` (and `YAML_*.cpp`) yield functions.
A green `status: ok` on the headers means nothing; see
[[bug-clang-annotator-silent-zero-functions]].

Annotate these 24 with `DFTRACER_CPP_FUNCTION()` + `DFTRACER_CPP_FUNCTION_UPDATE("comp", ...)`:

| File | Functions | comp |
| --- | --- | --- |
| `src/main.cpp` | `main` (REGION_START/END + INIT/FINI), `add_*_to_yaml` | `cpu` |
| `src/driver.hpp` | `driver` | `cpu` |
| `src/cg_solve.hpp` | `cg_solve` | `cpu` |
| `src/exchange_externals.hpp` | `exchange_externals`, `begin_*`, `finish_*` | `comm` |
| `src/make_local_matrix.hpp` | `make_local_matrix` | `comm` |
| `src/generate_matrix_structure.hpp` | `generate_matrix_structure` | `cpu` |
| `src/assemble_FE_data.hpp` | `assemble_FE_data` | `cpu` |
| `src/perform_element_loop.hpp` | `perform_element_loop` | `cpu` |
| `src/SparseMatrix_functions.hpp` | `matvec_std::operator()`, `matvec_overlap::operator()` | `gpu` |
| | `matvec`, `impose_dirichlet` | `cpu` |
| | `init_matrix`, `rearrange_matrix_local_external` | `mem` |
| | `write_matrix` | `io` |
| `src/Vector_functions.hpp` | `waxpby`, `daxpby`, `dot`, `dot_r2` | `cpu` |
| | `write_vector` | `io` |
| `utils/BoxPartition.cpp` | `box_partition` | `cpu` |
| `src/YAML_Doc.cpp` | `YAML_Doc::generateYAML` | `io` |

**Never annotate** these — they run per element/per timer-tick and will bury the trace:
`fem/Hex8.hpp`, `fem/matrix_algebra_3x3.hpp`, `fem/gauss_pts.hpp`,
`SparseMatrix_functions.hpp: sum_into_row / sum_in_elem_matrix / sum_in_symm_elem_matrix /
sum_into_global_linear_system`, `Vector_functions.hpp: sum_into_vector`,
`utils/mytimer.cpp: mytimer` (called from every `TICK()`/`TOCK()` in the CG loop).

The `matvec_std::operator()` body holds the `#pragma omp target teams distribute parallel
for` kernel; the RAII guard sits on the host stack above the pragma, which is correct and
does not perturb offload.

`main()` has **two** exits (`return 1` on the zero-equations path, `return return_code`),
each preceded by a `miniFE::finalize_mpi()` **wrapper** — not a literal `MPI_Finalize`.
REGION_END + FINI must go before *both* wrappers.

## Running

CLI is `key=value`: `miniFE.x nx=768 ny=768 nz=768 [verify_solution=1]`.

### `verify_solution=1` fails at every benchmark size — and it is not your fault

`driver.hpp` hard-caps `max_iters = 200`. `verify_solution` then compares the computed
value at ~(0.5,0.5,0.5) against the analytic solution with `tolerance = 0.06`. At anything
past a toy mesh the CG has not converged nearly far enough, so miniFE prints
`max absolute error is 0.20...` and **`main` returns 1**.

Measured, 16 ranks, `nx=256` and `nx=768`: the **pristine, un-annotated** binary produces
byte-identical residual sequences and the identical verify failure. Use that A/B before
blaming instrumentation for a non-zero exit. Standard benchmark runs simply omit
`verify_solution`.

### Sizing

`max_iters` is fixed at 200, so wall time scales with mesh size alone, not iteration count.
Measured on MI300A: `nx=64`, 1 rank → 0.35 s CG / 0.86 s total. Scale as `N^3 / ranks`:

| Config | Wall time |
| --- | --- |
| `nx=256`, 4N x 4R (16 ranks) | ~17 s |
| `nx=768`, 4N x 4R (16 ranks) | ~3 m 45 s |

`nx=768` at 16 ranks needs ~9 GB/rank for the CSR matrix — comfortable on a 128 GB APU.
Setup is a real fraction of the run (`make_local_matrix` alone was 3.5 s at `nx=256`), so
the trace has genuine comm-heavy setup as well as the solver loop.

## Known-good baseline (Tuolumne, 4 nodes x 4 ranks, `nx=ny=nz=768`)

1,165,631 events — 13/16 app ranks + 4/4 service nodes (the 3 missing ranks are the PAPI
teardown segfault below, not an annotation gap):

| Source | Events |
| --- | --- |
| PAPI (`CACHE`/`FLOP`/`BRANCH`/`TLB`/`INSTRUCTION`/`CYCLE`) | 738,930 |
| MPI (`p2p`/`collective`/`comm`) | 83,557 |
| HIP (`KERNEL_DISPATCH`/`MEMORY_COPY`/`PAGE_MIGRATION`) | 32,553 |
| `CPP_APP` (all 20 annotated functions) | 18,789 |
| `POSIX`/`STDIO` | 53,074 |
| `dftracer_service` node counters (`sys`/`io`/`net`) | 233,310 |

## PAPI teardown segfaults 1-3 ranks of 16

With `DFTRACER_ENABLE_PAPI_TRACING=1`, a minority of ranks SIGSEGV **after all science
completes**, during teardown, so their traces never flush. Isolated by A/B on identical
pinned hosts and arguments.

This is the known PAPI-sampler-vs-GOTCHA teardown race, whose fix has **not landed on
`feature/papi-counter-tracing`** — see [[system-tuolumne]] and the memory entry
`bug-dftracer-rocprofiler-configure-race-and-papi-sampler-segv`. Workaround:
`DFTRACER_ENABLE_PAPI_TRACING=0` gives a clean 16/16 at the cost of all hardware counters;
otherwise accept losing a few ranks, since the survivors' traces are complete and the run's
science is unaffected.

Red flag to check on any miniFE run: count zero-byte `*-app.pfw.gz` files. Each one is a
rank that died before flushing.

## Verifying the trace after the run

Two dftracer-utils gotchas bite here, both confirmed on this baseline:

- **`event_count` is approximate.** It prefixes its result with `~`. Raw vs split on the
  identical data reported 1,165,631 and 1,165,641 — a 10-event gap that is estimation, not
  loss. Do not chase it.
- **`stats --report summary`/`categories` returns nothing.** It reports
  `Events Scanned: 0` / `Categories (0)` even on a directory full of events (the indexer
  never scans event content). Get category counts from a direct gzip+json parse instead.

So the trustworthy check that a split is lossless is a per-category comparison of raw vs
split, not either tool's total. On this baseline that came out exactly equal across all 20
categories.

---

## The `cuda` variant (NVIDIA GPUs) — three build/run traps

miniFE's `cuda/` variant is the one to use on an NVIDIA machine (the
`openmp45*` variants target AMD offload). It is old code and does not build or
launch correctly out of the box on a modern CUDA/H100 system.

### 1. `num_devices` defaults to 2 and `cudaGetDeviceCount` is never called

`setCudaDeviceFromRank()` in `src/main.cu` picks the GPU as:

```c
if (params.num_devices < 0) cudaGetDeviceCount(&params.num_devices);  // never fires
...
local_rank = atoi(getenv("SLURM_LOCALID"));
params.device = local_rank % params.num_devices;
cudaSetDevice(params.device);
```

`num_devices` is a CLI parameter **defaulting to 2** (`utils/utils.cpp`), so the
`< 0` guard never triggers and the real device count is never queried.

Consequences on a 4-GPU node:

| launch | result |
|---|---|
| `--gpus-per-task=1` | Slurm exposes one GPU per task; rank 1 computes `1 % 2 = 1` → **`cudaErrorInvalidDevice: invalid device ordinal`**, rank 1 aborts, rank 0 then **hangs forever** in the next MPI collective |
| `--gres=gpu:4` alone | works, but `localid % 2` means ppn=4 uses only GPUs 0 and 1, two ranks per GPU — silently wrong for a ppn scaling study |
| **`--gres=gpu:4` + `num_devices=4`** | ✅ ranks map 1:1 onto GPUs for ppn ∈ {1,2,4} |

Always pass `num_devices=<gpus per node>` explicitly. Pair it with
`--kill-on-bad-exit=1` and an outer `timeout` so a device error cannot wedge a
sweep in a collective.

(`skip_device` defaults to 9999, so the `if (device >= skip_device) device++`
branch is inert — it is not part of this bug.)

### 2. CUDA 12: the legacy `<nvToolsExt.h>` collides with nvtx3

`src/CudaELLMatrix.hpp` does `#include <nvToolsExt.h>`. CUDA 12 ships **both**
that legacy header and `nvtx3/nvToolsExt.h`, and the CUDA runtime headers pull
in the nvtx3 copy — so the two definitions collide:

```
error: "NVTX_RESOURCE_TYPE_GENERIC_HANDLE" has already been declared in the current scope
error: invalid redeclaration of type name "nvtxResourceGenericType_t"
... 35 errors detected in the compilation of "main.cu"
```

Fix — change the include, do **not** delete the NVTX calls:

```c
#include <nvtx3/nvToolsExt.h>
```

miniFE only uses `nvtxRangeStartA` / `nvtxRangeEnd` / `nvtxRangeId_t`, all of
which nvtx3 provides. Keeping them is actively useful under dftracer: the CUPTI
backend surfaces NVTX markers as `CUDA_MARKER` events (see [[software-cupti]]).

### 3. nvcc: `-Xcompiler` splits on commas, so `-Wl,-rpath,X` breaks

Linking against a session-local dftracer needs an rpath, and nvcc mangles the
usual spelling two different ways:

| form | result |
|---|---|
| `-Wl,-rpath,/path` | `nvcc fatal : Unknown option '-Wl,-rpath,/path'` |
| `-Xcompiler "-Wl,-rpath,/path"` | nvcc splits the argument on commas → `g++: error: unrecognized command-line option '-Wl'` |
| **`-Xlinker -rpath -Xlinker /path`** | ✅ |

Working Makefile fragment for H100 + mvapich2 + a session dftracer:

```make
CUDA_HOME ?= /usr/tce/packages/cuda/cuda-12.6.0
NVCC       = nvcc -ccbin mpicxx -Xcompiler -fopenmp
NVCCFLAGS  = -lineinfo -gencode=arch=compute_90,code=\"sm_90,compute_90\"
LIBS       = -lnvToolsExt -L$(CUDA_HOME)/lib64 \
             -Xlinker -rpath -Xlinker $(CUDA_HOME)/lib64 \
             -L$(DFTRACER_DIR)/lib64 -ldftracer_core \
             -Xlinker -rpath -Xlinker $(DFTRACER_DIR)/lib64
```

`-ccbin mpicxx` lets the MPI wrapper carry all MPI include/link flags; the stock
Makefile hardcodes OpenMPI paths (`$(MPI_HOME)`, `-lmpi_cxx`) and `sm_35`, which
CUDA 12 no longer accepts at all.

### Annotating the cuda variant

`clang_extract_functions` cannot parse these headers as plain C++ (`threadIdx`,
`__longlong_as_double` undeclared). With
`-x cuda --cuda-host-only --cuda-path=<root>` plus `-I<root>/include` (for
`thrust/`) it parses cleanly — but then returns **0 functions**, because the
work lives in uninstantiated templates. That is the known template-header case,
not a tool bug: use the anchor-driven header annotator instead, and handle
`main.cu` separately (`DFTRACER_CPP_INIT` after `initialize_mpi`,
`DFTRACER_CPP_FINI` **before** `CudaManager::finalize()` and `finalize_mpi()` so
CUPTI's flush runs while the CUDA context is still alive).
