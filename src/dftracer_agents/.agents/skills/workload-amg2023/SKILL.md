---
name: workload-amg2023
description: AMG2023 (LLNL/AMG2023) — the hypre-based algebraic multigrid benchmark. Single-file C driver, its hypre dependency, the CPU MPI+OpenMP build recipe, why only ~3 functions are annotatable, and the full-feature dftracer event inventory measured on Tuolumne. Load this skill for any AMG2023 build, annotation, or trace session.
---

# workload-amg2023

AMG2023 (`LLNL/AMG2023`, LLNL-CODE-846758) is a parallel algebraic-multigrid
benchmark. It is a **thin driver over hypre** — the entire benchmark is one
3079-line C file, `amg.c`, and all the science lives in `libHYPRE`.

## Shape of the code (matters for annotation)

`amg.c` contains exactly **four** function definitions:

| function | annotate? |
| --- | --- |
| `main` | yes |
| `BuildIJLaplacian27pt` | yes |
| `BuildIJLaplacian7pt` | yes (but only called for other problem ids) |
| `hypre_map27` | **no** — a 6-line index mapper called from the innermost assembly loop |

So a correct full-project annotation yields **3 functions annotated, 1
cost-filtered** — `clang_annotate_project` reports exactly that. Three
functions is NOT a red flag here (contrast
[[bug-clang-annotator-silent-zero-functions]]); *zero* would be. At run time
with `-problem 1` you get **2 `C_APP` events per rank** (`main` +
`BuildIJLaplacian27pt`), because the 7pt builder is not called.

**Do NOT annotate hypre.** It is ~800 C files whose kernels sit in the
innermost loops of the multigrid V-cycle and run O(10^9) times per solve;
annotating them would dominate both runtime and trace volume. hypre is a
dependency, not the app. The MPI/POSIX/STDIO layers of the trace already
capture hypre's behaviour through brahma interception — in the measured run
below, hypre's MPI traffic alone produced **20.4 M p2p events** with zero
hypre annotation.

## Build

AMG2023 needs hypre >= 2.27.0 built first. Build it into the session
workspace; never rely on a system/Cray hypre.

```bash
git clone --depth 1 --branch v2.32.0 https://github.com/hypre-space/hypre.git $WS/hypre-src
cd $WS/hypre-src/src
./configure --prefix=$WS/hypre --with-MPI --with-openmp --enable-shared CC="$CC" CXX="$CXX"
make -j 8 && make install
```

Verify the config actually took, rather than trusting configure's exit code:

```bash
grep -E 'HYPRE_USING_(OPENMP|HIP|CUDA)|HYPRE_SEQUENTIAL|HYPRE_RELEASE_VERSION' $WS/hypre/include/HYPRE_config.h
# want: HYPRE_USING_OPENMP 1, HYPRE_SEQUENTIAL undef, HYPRE_USING_HIP/CUDA undef
```

AMG2023 itself has both a hand-edited `Makefile` (whose `HYPRE_DIR` is
hardcoded to someone's home directory) and a CMake build. **Use CMake** —
the Makefile also unconditionally links CUDA and Umpire paths that do not
exist on most machines.

```bash
cmake $WS/source -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER="$CC" -DCMAKE_CXX_COMPILER="$CXX" \
  -DHYPRE_PREFIX="$WS/hypre" -DAMG_WITH_MPI=TRUE -DAMG_WITH_OMP=TRUE
```

Notes:
- `CMakeLists.txt` refuses an in-source build; always configure out-of-source.
- It sets `LINKER_LANGUAGE CXX` even though the code is pure C, so the link
  step runs through `mpic++`.
- `AMG_WITH_OMP=TRUE` resolves to `-openmp` under Cray CCE and pulls in
  `libcraymp.so` — expected, not an error.

## Running

`-P p q r` is the process topology and `-n nx ny nz` is the grid **per rank**;
`p*q*r` must equal the rank count or the app prints
`Error: Invalid number of processors or processor topology` and calls `exit(1)`.
Because the per-rank size is fixed, this is a **weak-scaling** benchmark: the
Figure of Merit stays roughly constant as you add ranks.

Runtime is heavily **startup-dominated** at small sizes. Measured at 2 nodes x
8 ranks on Tuolumne (MI300A, CPU-only build):

| `-n` per rank | wall time |
| --- | --- |
| 90  | 17.6 s |
| 150 | 27.3 s |
| 240 | 70.8 s |

So to land a run in a 1-3 minute window, use `-n 240 240 240` at 16 ranks;
raising `-n` from 90 to 150 (4.6x the unknowns) only bought 1.55x the wall
time.

## Annotation caveats (found 2026-08-31)

`clang_annotate_project` got the function selection right but produced two
defects that had to be fixed by hand — **verify these on every AMG2023 run**:

1. **No `DFTRACER_C_INIT` / `DFTRACER_C_FINI` were inserted at all.** The
   entry-point pass did not fire, even though `main` was annotated with
   `FUNCTION_START`. Without `INIT` the logger never initialises. Likely
   because the signature is split across lines as
   `hypre_int\nmain( hypre_int argc,` with a non-`int` return type.
2. **`DFTRACER_C_FUNCTION_END()` was placed AFTER `hypre_MPI_Finalize()`.**
   `clang_lint_annotations` did not catch it, because its L3 rule looks for
   the literal `MPI_Finalize` and hypre spells it `hypre_MPI_Finalize`.
   Every MPI-wrapping library with a prefixed finalize has this blind spot.

Correct layout for `main`:

```c
   hypre_MPI_Init(&argc, &argv);
   DFTRACER_C_INIT(NULL, NULL, NULL);
   DFTRACER_C_FUNCTION_START();
   DFTRACER_C_FUNCTION_UPDATE_STR("comp", "cpu");
   ...
   DFTRACER_C_FUNCTION_END();
   DFTRACER_C_FINI();
   /* Finalize MPI */
   hypre_MPI_Finalize();
   return (0);
```

`main` has **five** exit paths that all need `END` + `FINI`: the usage-print
`exit(1)`, two `return (-1)` precond-check paths, and the normal `return (0)`.
Both `BuildIJLaplacian*` functions also carry their own `exit(1)` topology
check, which terminates the process bypassing `main`'s FINI — so they need
`FINI` too.

Also note `session_patch_build` links **`-ldftracer`**, which does not exist;
the FUNCTION-mode library is **`libdftracer_core.so`**. Patch the injected
`target_link_libraries` to `-ldftracer_core` or the annotated build fails to
link. See [[bug-session-patch-build-wrong-dftracer-libname]].

## Measured full-feature event inventory (Tuolumne, 2 nodes x 8 ranks, `-n 240`)

CPU MPI+OpenMP build, dftracer FUNCTION mode, PAPI + variorum + node service.
16/16 rank traces and 2/2 service traces, none zero-byte, app rc=0.

App (per-rank) traces — 20,597,216 events over 16 pids:

| category | count | note |
| --- | --- | --- |
| `p2p` | 20,366,777 | `MPI_Iprobe` 9.99M, `MPI_Testall` 6.00M, `MPI_Test` 3.97M — hypre's async assembly polls hard |
| `comm` | 87,785 | `MPI_Comm_rank`/`MPI_Comm_size` |
| `STDIO` | 66,540 | `fgets`/`fopen`/`fclose` — almost all `/proc` and `/sys` reads, not app I/O |
| `dftracer` | 29,632 | internal (FH/CM/SH/HH) |
| `POSIX` | 22,624 | `access` 22,208 |
| `env` | 9,979 | `MPI_Get_count`, `MPI_Wtime` |
| `papi` | 8,733 | see below |
| `collective` | 5,114 | `MPI_Allreduce` 3,584, `MPI_Bcast` 928, `MPI_Scan` 416 |
| `C_APP` | 32 | exactly 2/rank: `main`, `BuildIJLaplacian27pt` |

Service (per-node) traces — 40,108 events, 2 files:

| category | count | note |
| --- | --- | --- |
| `sys` | 28,712 | per-core utilisation, `cpu` + `cpu-0`..`cpu-95`, 148 samples each |
| `io` | 10,360 | node-level I/O counters |
| `net` | 888 | node-level network counters |
| `gpu` | 148 | **variorum type-13 power**, keys `socket_0.GPU_0`..`socket_3.GPU_3`, `num_gpus_per_socket` |

`type` field: app traces are type 10 (p2p), 3, 1, 11 (papi), 2; service traces
are type 7 (counters) plus **type 13** (variorum power).

### PAPI

All four requested presets landed, each with a `_delta` companion:
`PAPI_TOT_CYC`, `PAPI_TOT_INS`, `PAPI_FP_OPS`, `PAPI_FP_INS` (8,733 each).

**`multiplex` is context-dependent**: `multiplex=0` on the compute nodes
(the 4 presets fit inside the 5 hardware counters natively), but the same
4-counter request on a **login node** reported `multiplex=1`. Always read
`multiplex` from the trace of the run you actually care about — do not infer
it from a probe run on a different node type. See [[software-papi]].

### Expected-absent layers on this build

- **GPU `KERNEL_DISPATCH` / `MEMORY_COPY`** — absent by design: this is a CPU
  build (`hypre` without `--with-hip`, `DFTRACER_HIP_TRACING_ENABLE` undef).
  Not a defect. The `gpu` category that *is* present is variorum node power,
  which is a different thing entirely.
- **`HDF5`** — absent; AMG2023 does no HDF5 I/O and dftracer was built with
  `DFTRACER_ENABLE_HDF5=OFF`.
- **CPU/RAPL power domains** — variorum reports only the AMD GPU domain.
  MSR/RAPL access is denied to non-root on Tuolumne (see [[system-tuolumne]]).

## Related

[[system-tuolumne]] · [[software-papi]] · [[tools-dftracer]] ·
[[dftracer-annotate-c]] · [[software-cmake]]
