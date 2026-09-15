---
name: workload-xsbench
description: XSBench (ANL-CESAR) — the Monte Carlo neutronics macroscopic cross-section lookup proxy app. Which of its six variants to build, the plain-make/MPI=yes build recipe, the hot-loop annotation trap that silently makes the app ~20x slower, run sizing, and the checksum warning that is NOT an annotation defect. Load this skill for any XSBench build, annotation, or tracing session.
---

# workload-xsbench

XSBench is a small (6 C files, ~1500 LOC) proxy app for the continuous-energy
macroscopic neutron cross-section lookup kernel of Monte Carlo neutron
transport. It is compute/memory-latency bound — a random-access gather over a
large unionized energy grid — and does essentially **no application I/O and
almost no MPI**.

## Variant selection

The repo root holds one self-contained directory per programming model, each
with its own `Makefile` and no top-level build system (so `session_detect`
reports `build_tool: unknown`):

| Directory | Language | MPI | Notes |
| --- | --- | --- | --- |
| `openmp-threading` | C | **`MPI=yes` switch in the Makefile** | The default/reference CPU version. Pick this. |
| `openmp-offload` | C | no Makefile switch | OpenMP 4.5 GPU offload |
| `cuda` | CUDA | no Makefile switch | |
| `hip` | C++ | **no Makefile switch** | sources have `#ifdef MPI` guards but the Makefile never defines `-DMPI` or links MPI |
| `opencl`, `sycl` | C / C++ | no | |

**Choose `openmp-threading` for a CPU MPI+OpenMP session.** It is the only
variant whose Makefile can turn MPI on without editing it. The `hip` variant
would need hand-added `-DMPI` plus MPI include/link flags on top of `hipcc`, so
it is not a "builds cleanly with little effort" alternative when MPI events are
wanted.

Because each variant is a separate directory, `session_detect` finds `hip/` and
`cuda/` in the tree and reports `hip: true` / `hip_tracing_needed: true` even
when you are building the CPU variant. It also reports `hdf5: true` purely from
a **system** `h5cc` — `hdf5_in_source` is `false` and XSBench uses no HDF5 at
all. Pass `hip=False, hdf5=False` explicitly to `session_install_dftracer`.

## Build

Plain GNU make, no configure step:

```bash
# after the site module load, per [[feedback-cc-cxx-mpi-selection]]
export CC=`which mpicc`
export CXX=`which mpic++`
cd <WS>/source/openmp-threading
make MPI=yes -j8
```

Two Makefile quirks worth knowing:

* `MPI=yes` **overrides `CC` to the bare string `mpicc`**, so `mpicc` must be on
  `PATH`; the full path you exported in `CC` is discarded.
* `-flto -fopenmp -DOPENMP` are added only if `$(CC)` contains the substring
  `gcc`, `clang`, or `intel`. On Cray PE the wrapper path contains
  **`crayclang`**, so the `clang` branch matches and OpenMP is enabled *before*
  `MPI=yes` rewrites `CC`. If you set `CC` to a path without one of those
  substrings you silently get a **serial, un-optimized** build. Always confirm
  `-fopenmp -DOPENMP` appears in the compile lines.

For the annotated build, pass the dftracer include/lib through the Makefile's
own variables (it has no pkg-config integration):

```
make MPI=yes -j8 \
  CFLAGS="-std=gnu99 -Wall -O3 -fopenmp -DOPENMP -DMPI -I<dft>/include" \
  LDFLAGS="-lm -L<dft>/lib64 -ldftracer_core -Wl,-rpath,<dft>/lib64"
```

Note that overriding `CFLAGS` wholesale drops the `-flto` the Makefile would
have added — harmless, and it keeps the annotated and baseline objects
comparable.

## THE annotation trap: never annotate the per-lookup kernels

`clang_annotate_project` instruments **every** non-trivial function, and in
`Simulation.c` that includes the innermost cross-section lookup kernels:

```
calculate_micro_xs, calculate_macro_xs, grid_search,
grid_search_nuclide, pick_mat, fast_forward_LCG
```

These are called **once per lookup — hundreds of millions to billions of times**
(`calculate_micro_xs` runs once per lookup *per isotope in the material*). The
AST cost heuristic cannot see call frequency, so it happily annotates them.

**Symptom:** the app appears to hang. A configuration that runs in ~18 s
un-annotated did not finish in **380 s** annotated, *even with
`DFTRACER_ENABLE=0`* — the per-call overhead alone is enough. With tracing on it
would also produce a multi-gigabyte trace of pure noise.

**Root cause:** function-level instrumentation placed inside a
hundreds-of-millions-iteration hot loop.

**Exact fix** — re-annotate that one file with the kernels excluded:

```
clang_annotate_file(
  filepath="source/openmp-threading/Simulation.c", language="c", is_entry=False,
  exclude_functions='["calculate_micro_xs","calculate_macro_xs","grid_search",
                      "grid_search_nuclide","pick_mat","fast_forward_LCG",
                      "LCG_random_double","quickSort_parallel_internal_i_d",
                      "quickSort_parallel_internal_d_i"]')
```

That leaves only the three outer drivers — `run_event_based_simulation`,
`run_history_based_simulation`,
`run_event_based_simulation_optimization_1` — which are entered once per rank
and are exactly the spans you want. Restore the pristine file from `source/`
before re-annotating so macros are not inserted twice.

The other files need no exclusions, but check that the `qsort` comparators in
`XSutils.c` (`NGP_compare`, `double_compare`) stay unannotated — they are
O(n log n) hot too. The default cost filter already skips them.

**Generalizable rule:** in any proxy app, after `clang_annotate_project`, list
the functions it annotated and ask of each "how many times is this called per
run?". Anything on a per-element/per-iteration path must go in
`exclude_functions`.

## Second annotation trap: `main()`'s dftracer macros land inside `#ifdef MPI`

`Main.c` wraps `MPI_Init`/`MPI_Comm_rank` in `#ifdef MPI`. The annotator inserts
`DFTRACER_C_INIT` + `FUNCTION_START` immediately after the last statement it
sees there — i.e. **inside the `#ifdef`** — while leaving the early-exit
`DFTRACER_C_FUNCTION_END()` on the `kernel_id` error path **outside** it. In a
non-MPI build that is a hard compile error (`use of undeclared identifier
'data_fn'`), and it makes all tracing conditional on MPI.

Fix: move `INIT`/`START`/metadata out of the `#ifdef MPI` (still *after*
`MPI_Init`, which is required), and make the closing `END`/`FINI` unconditional,
ahead of the `#ifdef MPI ... MPI_Finalize() ... #endif` block.

Also add `DFTRACER_C_FINI()` next to the `DFTRACER_C_FUNCTION_END()` the
annotator places before `exit(4)` in `io.c`'s `print_CLI_error()` — it is a real
process exit path reached after `INIT`.

## `validate_annotations` false positive

It reports `critical I/O flow not annotated ... calls: read` for
`quickSort_parallel_i_d` / `quickSort_parallel_d_i`. Those functions do no I/O —
the checker substring-matches `read` inside the parameter name **`numThreads`**.
Ignore it; confirm with a word-boundary search for an actual `read(` call.

## Run sizing and CLI

```
./XSBench -m event -s large -l <lookups> -t <threads> -G unionized
```

* `-s large` = 355 nuclides, 4,012,565 unionized gridpoints, **~5.6 GB per
  rank** — budget memory per rank, not per node.
* Every MPI rank independently performs the full `-l` lookups (ranks are
  replicas; the only MPI is a final `MPI_Barrier` + `MPI_Reduce`), so wall time
  depends on `-l` and threads, **not** on rank count.
* Grid initialization is ~6-8 s of the run and is deliberately named
  `grid_init_do_not_profile`.
* Reference throughput on an AMD MI300A CPU side, 22 OpenMP threads/rank:
  **~4.0-5.1 M lookups/s per rank**. So
  `wall ≈ 8 s + (lookups / 4.5e6)`. `-l 200000000` gives ~55 s of simulation.
* Full-feature dftracer tracing (PAPI at 100 ms + POSIX/STDIO/MPI interception)
  costs about **21 %** throughput (5.12 → 4.04 M lookups/s per rank).

### `INVALID CHECKSUM` is expected, and rc=1 with it

XSBench only carries reference verification checksums for its canonical `-l`
values. Any other `-l` prints
`Verification checksum: <n> (WARNING - INVALID CHECKSUM!)` and `main` returns
non-zero. **This is not an annotation defect.** Prove it the cheap way: run the
*unmodified* binary with identical arguments and compare — a correct annotation
reproduces the checksum **bit-for-bit** (observed: both binaries returned
`437675` at `-l 200000000`, 8 ranks, 22 threads). Do that comparison before
blaming instrumentation for any XSBench result difference.

## What a full-feature trace actually contains

Measured on 2 nodes x 4 ranks x 22 threads, `-s large -l 200000000`,
FUNCTION mode, with `dftracer_service` node counters and Variorum:

| Layer | Present | Notes |
| --- | --- | --- |
| `C_APP` | yes (~10/rank) | one span per outer function; tiny by design |
| `STDIO`, `POSIX` | yes (thousands) | **none of it is XSBench's own I/O** — it is loader/libc startup (`/proc/<pid>/maps`, `fgets`, `access`). XSBench reads no input deck. |
| `collective` | yes, exactly 2/rank | `MPI_Barrier` + `MPI_Reduce`. There is no p2p and no other collective anywhere in the app. |
| `papi` | yes | one type-11 record per sample interval per rank |
| `sys` / `io` / `net` | yes | from `dftracer_service`, per node, type 7 |
| `gpu` power (type 13) | yes | Variorum **node power telemetry, NOT GPU tracing** — see warning below |
| `KERNEL_DISPATCH` / `MEMORY_COPY` | no | CPU variant has no GPU work |

> **Do not read `cat: "gpu"` as "GPU tracing worked."** Variorum emits a `gpu`
> power category on a **CPU-only** build too, because it samples the node's GPUs
> from outside the application via rocm_smi. Those are type-13 counter records
> named `power` carrying per-socket watts (`socket_N.GPU_N`), produced by
> `dftracer_service`, and they appear even when
> `DFTRACER_HIP_TRACING_ENABLE` is undefined. Real GPU *tracing* means
> `KERNEL_DISPATCH` / `MEMORY_COPY` / `HIP_RUNTIME_API` events in the **per-rank
> app** traces. Check which file the events came from before claiming GPU
> coverage.

The takeaway for anyone expecting an I/O story: **XSBench has no application
I/O.** Every POSIX/STDIO event is process startup. Do not size an I/O study
around it; use it for compute/memory and PAPI work.

## See also

* [[system-tuolumne]] — module order, PAPI counter set, Flux.
* [[software-papi]] — counter budget and how to verify `multiplex: 0`.
* [[dftracer-annotate-c]] — INIT/FINI placement rules.
* [[tools-dftracer]] — install source selection.
