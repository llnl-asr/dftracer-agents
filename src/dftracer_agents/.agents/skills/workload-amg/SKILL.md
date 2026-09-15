---
name: workload-amg
description: AMG (LLNL/AMG) — the hypre-based algebraic-multigrid solver proxy app in ISO-C, MPI+OpenMP, plain Makefile. Build defects that only newer compilers surface, the Makefile.include integration point that build-patch tooling misses, the annotation traps in hypre's brace-free style, the hot functions that dominate a trace, and the verified full-feature dftracer baseline recipe. Load this for any AMG or hypre-proxy session.
---

# workload-amg

**AMG** (`LLNL/AMG`) is a parallel algebraic-multigrid solver proxy app built on a
trimmed copy of hypre. ISO-C, MPI + OpenMP threading within ranks, decomposed as a
logical `P x Q x R` grid of equal chunks. Driver is `test/amg.c`; libraries live in
`utilities/ krylov/ IJ_mv/ parcsr_ls/ parcsr_mv/ seq_mv/`. 84 `.c` files total.

Related: [[system-tuolumne]], [[software-papi]], [[dftracer-annotate-c]],
[[dftracer-trace-utils]], [[flux-alloc]].

## Build

Plain hand-written Makefiles. Every subdirectory `include ../Makefile.include`, so
**`Makefile.include` is the single integration point** — its `INCLUDE_CFLAGS` and
`INCLUDE_LFLAGS` are the only variables that reach every compile and link.

Default `INCLUDE_CFLAGS` is already the wanted MPI+OpenMP CPU configuration:
`-O2 -DTIMER_USE_MPI -DHYPRE_USING_OPENMP -fopenmp -DHYPRE_HOPSCOTCH
-DHYPRE_USING_PERSISTENT_COMM -DHYPRE_BIGINT`. Load site modules, then
`export CC=$(which mpicc)` and just `make -j 8`.

### Upstream defect: `HYPRE_BoomerAMGGetCumNnzAP` is not declared in the public header

`test/amg.c` calls it, but it is declared only in the INTERNAL
`parcsr_ls/_hypre_parcsr_ls.h`, never in the public `parcsr_ls/HYPRE_parcsr_ls.h`
that `amg.c` includes. Older compilers accepted the implicit declaration; **clang >= 15
and Cray clang 20 treat it as a hard error**, so the pristine app does not build:

```
amg.c:405:7: error: call to undeclared function 'HYPRE_BoomerAMGGetCumNnzAP';
             ISO C99 and later do not support implicit function declarations
```

Fix — add the prototype to the public header next to `HYPRE_BoomerAMGSetNumPaths`:

```c
HYPRE_Int HYPRE_BoomerAMGGetCumNnzAP(HYPRE_Solver solver, HYPRE_Real *cum_nnz_AP);
```

The symbol is already defined in `parcsr_ls/HYPRE_parcsr_amg.c` and lands in
`libparcsr_ls.a`, so nothing else changes. Do NOT reach for
`-Wno-implicit-function-declaration`; the declaration is the real fix.

### The top-level `make` HIDES subdirectory failures

`Makefile`'s `all:` target is a shell `for` loop over `HYPRE_DIRS` with no error
propagation, so **the outer `make` exits 0 even when several subdirectory builds
failed**. Any wrapper that trusts the return code (including
`session_build_annotated`, which reported "Annotated build succeeded" on a build
that produced no binary) will report a false success. Always gate on the artifact
and the log instead:

```bash
grep -c "error:" build.log            # must be 0
ls -la test/amg                       # must exist
```

## dftracer integration

`session_patch_build` detects `build_tool="make"` and injects autotools-style
`AM_CPPFLAGS`/`AM_CXXFLAGS`/`AM_LDFLAGS` into every Makefile. **AMG's hand-written
Makefiles never reference those variables, so the patch is a silent no-op.** Wire the
flags into AMG's own variables in `Makefile.include`:

```make
INCLUDE_CFLAGS = ...existing... $(DFTRACER_CFLAGS)
INCLUDE_LFLAGS = -lm -fopenmp $(DFTRACER_LDFLAGS) -ldftracer_core
```

`INCLUDE_LFLAGS` is appended at the end of `test/Makefile`'s `LFLAGS`, after the
`-lparcsr_ls ... -lHYPRE_utilities` static archives, so `-ldftracer_core` resolves
correctly without further ordering work. Verify with
`ldd test/amg | grep dftracer`.

## Annotation

### Entry point is excluded by default

`main()` lives in `test/amg.c`, and `clang_annotate_project` **always** excludes
`/test/` regardless of `exclude_patterns`. Annotate the project first, then make a
separate `clang_annotate_file(filepath="source/test/amg.c", is_entry=True)` call, or
the binary gets no `DFTRACER_C_INIT`/`FINI` at all.

### INIT/FINI must be moved by hand around hypre's MPI wrappers

The annotator places `DFTRACER_C_INIT` at the top of `main`'s body and `FINI` after
the MPI teardown. AMG wraps MPI as `hypre_MPI_Init` / `hypre_MPI_Finalize`, which the
annotator does not recognise, so both end up on the wrong side:

* `DFTRACER_C_INIT` must move to immediately AFTER `hypre_MPI_Init(&argc, &argv);`
  (~line 145). Everything above it in `main` is C89 declarations only, so moving
  `INIT` + `FUNCTION_START` + the `comp` UPDATE down together loses no coverage.
* `DFTRACER_C_FINI()` must move to BEFORE `hypre_MPI_Finalize();`.
* `main` has three more exits: `exit(1)` in the usage/arg-check path and two
  `return(-1)` paths. Each needs `FUNCTION_END` -> `FINI`. There is a fourth
  `exit(1)` in the `P*Q*R != num_procs` check inside the grid-builder function,
  which also needs a `FINI`.

Any app that wraps MPI behind its own `*_MPI_Init` name has this problem — check for
the wrapper, not just for `MPI_Init`.

**`clang_lint_annotations` cannot catch any of this.** Its L3 rule ("FINI before
finalize") matches only the literal string `MPI_Finalize`. Against hypre's
`hypre_MPI_Finalize` the rule never fires, so a file with `FUNCTION_END`/`FINI` on
the WRONG side of finalize lints 100% clean. All 68 annotated AMG files passed L1-L5
with zero findings while `FINI` was still sitting after `hypre_MPI_Finalize`. Treat a
clean lint on a wrapper-using app as no evidence at all — grep the entry-point file
for `DFTRACER_C_INIT` / `DFTRACER_C_FINI` and read the surrounding lines yourself.

`validate_annotations` errs the other way: it reported "no dftracer init found", "no
dftracer finalize found" and "no app-parameter metadata" for this tree when all three
were present and correct in `test/amg.c`. Both directions of error occur, so verify
against the source and then against the trace.

### hypre's brace-free style breaks macro insertion — verify explicitly

hypre is full of braceless single-statement bodies. If the annotator's brace pass is
skipped (see below), `DFTRACER_C_FUNCTION_END()` is inserted between the control head
and its body and the guarded statement becomes **unconditional**:

```c
   if (hypre_global_timing == NULL)
      DFTRACER_C_FUNCTION_END();
      return ierr;              /* now ALWAYS returns */
```

22 such sites were produced in one pass across `IJ_mv/HYPRE_IJMatrix.c`,
`IJ_mv/IJMatrix_parcsr.c`, `IJ_mv/IJVector.c`, `IJ_mv/aux_parcsr_matrix.c`,
`parcsr_ls/par_cycle.c`, `parcsr_ls/par_interp.c`, `parcsr_ls/par_nongalerkin.c`,
`seq_mv/csr_matop.c` and `utilities/timing.c`. Only a handful (the `if/else`
pairs in `HYPRE_IJMatrix.c` and `IJVector.c`) fail to COMPILE — the rest compile
cleanly and silently corrupt control flow.

Two of the 22 were separated from the control head by a BLANK LINE, so a scanner that
only inspects the immediately-preceding line misses them. Skip blank lines when
looking backwards.

**Always confirm semantics numerically**: run the annotated binary with
`DFTRACER_ENABLE=0` and check it reproduces the pristine run's iteration count and
final residual exactly (`Iterations = 17`, `Final Relative Residual Norm =
6.589910e-09` for `-P 1 1 1 -n 20 20 20 -problem 1`). Wall-time-derived numbers
(the FOM) legitimately differ; iteration counts and residuals must not.

### Pass real include paths or the annotation is silently degraded

Without `compile_flags`, clang cannot find `_hypre_utilities.h` and falls back to an
error-recovery AST. The tool still reports `status: ok`, but the difference is large:

| | no include paths | with include paths |
|---|---:|---:|
| functions annotated | 251 | **388** |
| `par_amg.c` | 21 | **53** |
| `HYPRE_IJMatrix.c` insertions | 30 | **93** |
| brace insertion | SKIPPED | still skipped (see below) |

Working flag set (absolute paths, applies to every file):

```
-I<ann>/source -I<ann>/source/utilities -I<ann>/source/IJ_mv -I<ann>/source/seq_mv
-I<ann>/source/parcsr_mv -I<ann>/source/parcsr_ls -I<ann>/source/krylov
-I<cray-mpich>/include
-DHYPRE_BIGINT -DTIMER_USE_MPI -DHYPRE_USING_OPENMP -DHYPRE_HOPSCOTCH
-DHYPRE_USING_PERSISTENT_COMM -DHYPRE_TIMING
```

Confirm the flags work before annotating: `clang -fsyntax-only $FLAGS <file>.c`
must be silent.

### The `#include <dftracer/dftracer.h>` can land at the BOTTOM of a file

The annotator inserts the include after the LAST `#include` line in the file, and it
counts includes that appear deep in the body — including ones inside commented-out
blocks. In `IJ_mv/IJMatrix_parcsr.c` it landed at line 2931 and in
`parcsr_ls/par_interp.c` at line 4312, after nearly every annotated function, giving
`call to undeclared function 'DFTRACER_C_FUNCTION_START'`. Check that the include line
number is LOWER than the first `DFTRACER_C_` line in every file.

### `annotate_add_app_metadata(expressions=True)` emits integers, which do not compile

`DFTRACER_C_METADATA(name, key, value)` takes `value` as `const char *`. Passing a
numeric expression gives
`incompatible integer to pointer conversion ... to parameter of type 'ConstEventNameType'`.
Stringify first:

```c
{  char dft_mbuf[64];
   hypre_sprintf(dft_mbuf, "%d", (int)(problem_id));
   DFTRACER_C_METADATA(dft_meta_problem_id, "problem_id", dft_mbuf);
   ... }
```

Also check the anchor's SCOPE: `nx/ny/nz/P/Q/R/num_procs` are locals of the
grid-builder function, NOT of `main`. From `main` the available run parameters are
`problem_id, solver_id, max_iter, mg_max_iter, coarsen_type, relax_type, num_sweeps,
agg_num_levels, P_max_elmts, num_functions, time_steps`; get the rank count with a
local `hypre_MPI_Comm_size(hypre_MPI_COMM_WORLD, &n)`.

### Hot-function exclusions

Exclude these outright — they are per-element inner-loop utilities and produce nothing
but volume:

```
utilities/binsearch.c            utilities/hypre_hopscotch_hash.c
utilities/hypre_qsort.c          utilities/hypre_merge_sort.c
utilities/qsplit.c               utilities/threading.c
utilities/timer.c                utilities/hypre_complex.c
utilities/random.c               utilities/hypre_error.c
utilities/hypre_printf.c         utilities/amg_linklist.c
utilities/hypre_prefix_sum.c     utilities/mpistubs.c
utilities/umalloc_local.c        utilities/memory_dmalloc.c
```

**That list is not sufficient.** hypre defines a SECOND sorting routine outside
`utilities/`: `hypre_qsort2abs` in `parcsr_ls/par_interp.c:3187`. It alone produced
**733,521 of 1,023,178 C_APP events (72%)** in a 13-second 8-rank run. Add it to
`exclude_functions` (function-level, not file-level — the rest of `par_interp.c` is
worth keeping). `hypre_CSRMatrixGetLoadBalancedPartitionBoundary` is the next
offender at 131,444 events; exclude it too unless you specifically want load-balance
partitioning data.

## Run sizing (measured, Tuolumne, annotated + full tracing)

`-P p q r` must multiply to the rank count; `-n nx ny nz` is the grid PER RANK.

| config | wall | trace |
|---|---:|---:|
| 1 rank, `-P 1 1 1 -n 20 20 20` | ~1 s | tiny (smoke) |
| 2 nodes x 4 ranks, `-P 2 2 2 -n 80 80 80` | 16 s | 8 x ~1.15 MB gz |
| 2 nodes x 4 ranks, `-P 2 2 2 -n 100 100 100` | 13 s | 8 x ~1.6 MB gz, 1.31 M events |

AMG is FAST — `-n 100` per rank still finishes in seconds. Do not assume a long run
is compute-bound; a multi-minute "run" is far more likely to be a Flux job stuck in
state `S` waiting for cores the `dftracer_service` launcher job is holding (see
[[system-tuolumne]]). Check `flux jobs -a` before enlarging the problem.

`-problem 1` prints `Iterations`, `Final Relative Residual Norm`, and three FOM
lines; the iteration count and residual are the correctness invariants to compare
against a pristine build.

## Verified full-feature baseline (2 nodes, MI300A, Tuolumne)

8 ranks (2 nodes x 4), `-P 2 2 2 -n 100 100 100 -problem 1`, `OMP_NUM_THREADS=8`,
`-c 20` per rank, rc=0, 13 s wall, **1,318,447 events**, all 8 ranks flushed a
trailing `end` event.

| layer | events | note |
|---|---:|---|
| `C_APP` (annotation) | 1,023,178 | 150 distinct functions of 388 annotated |
| `p2p` | 195,417 | Iprobe/Testall/Irecv/Isend dominate |
| `STDIO` | 33,004 | `/proc` + hypre reads |
| `comm` | 25,358 | Comm_rank/Comm_size |
| `POSIX` | 11,312 | mostly `access` |
| `env` | 1,841 | MPI_Get_count, MPI_Wtime |
| `collective` | 1,507 | Allreduce 1200, Bcast 136, Allgatherv 72, Scan 48, Barrier 32 |
| `papi` | 208 | family-grouped, **multiplex=0** |
| service `sys`/`io`/`net` | 9,700 / 1,700 / 300 | per-node counters, both nodes |
| service `gpu` power (type 13) | 50 | 25/node x 4 GPU sockets, variorum |

PAPI set `PAPI_TOT_CYC,PAPI_TOT_INS,PAPI_FP_OPS,PAPI_FP_INS` is **EXACT** on MI300A
(`multiplex=0`, all four keys present) even though `PAPI_FP_INS` is a derived preset
and the node has only 5 hardware counters. Verified from the trace, not assumed.

GPU `KERNEL_DISPATCH` / `MEMORY_COPY` are legitimately ABSENT: AMG is a CPU-only
MPI+OpenMP code, built with `hip=False` / `DFTRACER_HIP_TRACING_ENABLE` undefined.
Their absence is expected, not a defect. The `gpu`-category records that DO appear
come from variorum in the SERVICE trace, not from the app.

`comp` classification of annotated spans came out `cpu` 907,346 / `mem` 115,832 and
no `io`/`comm` — correct for AMG, whose only real I/O is `/proc` probing and whose
MPI traffic is captured by the built-in interceptors as `p2p`/`collective`/`comm`
rather than by app annotation.

## Sweeping AMG across a configuration space: `-P` must be DERIVED, not swept

`merged.txt` gives 16 unique argument strings for `amg`, but `-P p q r` is the rank
decomposition and `p*q*r` must equal the rank count. ice4hpc only ever ran
`-P 1 1 1` (1 rank) and `-P 4 4 2` (32 ranks), so holding `-P` fixed pins the whole
sweep to two rank counts and destroys the node/ppn dimensions.

Treat the **`(problem, -n)` pair as the input** and re-derive `-P` per cell. The 16
argument strings map 1:1 onto 16 distinct `(problem, -n)` cases — a bijection, so
nothing is dropped. `-n` is the grid PER RANK, so this is a weak-scaling sweep.

Derive by balanced binary factorisation, `2^k -> (2^a,2^b,2^c)`, `a>=b>=c`:

| ranks | 1 | 2 | 4 | 8 | 16 | 32 | 64 | 128 | 256 | 512 |
|---|---|---|---|---|---|---|---|---|---|---|
| `-P` | 1 1 1 | 2 1 1 | 2 2 1 | 2 2 2 | 4 2 2 | **4 4 2** | 4 4 4 | 8 4 4 | 8 8 4 | 8 8 8 |

This **reproduces ice4hpc's own decompositions exactly** at both rank counts it
recorded (1 -> `1 1 1`, 32 -> `4 4 2`), which is the check that the rule is the one
the app's authors intended rather than an invention.

## Per-rank memory by problem size (measured, 1 rank, untraced)

`-n` is per rank, so a case that is trivial at ppn=1 can be impossible at ppn=64.

| case | peak RSS | | case | peak RSS |
|---|---:|---|---|---:|
| `p1_n8x8x4` | 30 MB | | `p2_n32x32x32` | 91 MB |
| `p1_n32x32x16` | 47 MB | | `p2_n64x64x32` | 273 MB |
| `p1_n64x64x32` | 166 MB | | `p2_n64x64x64` | 515 MB |
| `p1_n128x128x64` | 1.10 GB | | `p2_n128x128x64` | 1.94 GB |
| `p2_n16x16x16` | 38 MB | | `p2_n128x128x128` | 3.85 GB |
| | | | **`p2_n256x256x256`** | **30.8 GB** |

Cap the ppn ladder from these, per case, rather than discovering it as a wall of OOM
failures deep into a sweep.

## At high rank counts on a small per-rank grid, AMG is a `p2p` FIREHOSE

AMG's MPI traffic is `Iprobe`/`Testall` polling, so the event count explodes as ranks
grow and per-rank work shrinks — the opposite of intuition. Measured with MPI
protected from aggregation:

* `p2_n8x8x8` at 128 ranks: **384 M events, 99.8 % of them `p2p`**
* `p2_n64x64x32` at 128 ranks: **797 M events**, `p2p` 794 M
* trace bytes scale as roughly **ranks^1.4-1.9**, not ranks^2

Budget for this when sizing a corpus, and see the aggregation note below.

## Aggregation: fold the app layer, NEVER the MPI layer

`dur < 100` (microseconds) applied to everything destroys exactly what an AMG trace
is for. Measured at 4 ranks, `-problem 2 -n 64 64 64`:

| rule | total events | `C_APP` | MPI kept |
|---|---:|---:|---|
| no aggregation | 1,387,315 | 996,585 | all |
| **`dur<100` excluding `collective,comm,p2p`** | **260,832** | 75,875 (7.6 %) | **all** |
| `dur<100` on everything | 85,260 | 69,484 | **`p2p` 2,421 of 140,392** |

The exclusion also neutralises the `hypre_qsort2abs` volume (72 % of `C_APP` events)
without editing the source, which is the cheaper fix than stripping its annotation.
Read fold counts from **`dft_cnt`**, never `count` (POSIX read/write already use
`count` for syscall *bytes*).

## The annotated tree is semantics-preserving — verify it per machine

Annotated binary with `DFTRACER_ENABLE=0`, `-problem 1 -P 1 1 1 -n 20 20 20`:

```
Iterations = 17
Final Relative Residual Norm = 6.589910e-09
```

An exact match of the pristine reference, to all seven digits. hypre's brace-free
style means an inserted `FUNCTION_END` can make a guarded `return` unconditional —
which compiles cleanly and silently corrupts control flow — so this equality check is
the one that matters, not a clean lint.
