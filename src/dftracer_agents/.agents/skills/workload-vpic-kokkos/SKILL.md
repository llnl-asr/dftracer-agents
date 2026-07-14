---
name: workload-vpic-kokkos
description: >
  VPIC-Kokkos-specific knowledge: build/annotation quirks, the two-stage
  deck-compile pattern, deck sizing for smoke vs validation runs, and the
  measured MPI-communication-bound diagnosis on Tuolumne. Load this skill
  whenever working with vpic-kokkos (github.com/lanl/vpic-kokkos).
---

Cross-references: [[dftracer-annotation-lessons]] [[dftracer-diagnoser]] [[software-mpi]] [[bug-dftracer-crayclang-python-abi]]

---

## Build

- VPIC is CPU-only by default on this codebase (Kokkos Serial+OpenMP backend,
  bundled Kokkos submodule) — do not assume HIP/CUDA offload just because the
  target has GPUs; check the app's own CMake defaults first.
- CMake flags: `-DBUILD_INTERNAL_KOKKOS=ON -DENABLE_KOKKOS_OPENMP=ON -DENABLE_KOKKOS_CUDA=OFF`.
- Two-stage build: CMake produces a `vpic` compiler-wrapper script
  (`install/bin/vpic`), NOT a runnable simulation binary. Each input deck
  (`.cxx`) is compiled into its own executable by running
  `install/bin/vpic path/to/deck.cxx`, which internally invokes the real MPI
  C++ compiler with the deck + `deck/main.cc` + `deck/wrapper.cc` and links
  against `libvpic.a`.
- When building an ANNOTATED copy under a different install prefix
  (`install_ann/` vs `install/`), the generated `vpic` wrapper script may be
  missing pieces the original didn't need to regenerate: check for/symlink
  `install_ann/deck` → the annotated `deck/` sources (`main.cc`/`wrapper.h`
  are installed to `share/vpic/`, not always to a `deck/` subdir), symlink
  `install_ann/src` → the annotated source's `src/` (header search path used
  by the deck-compile step), and symlink `install_ann/libvpic.a` at the
  install root if the generated script's `-L` list doesn't include `lib/`.
  The dftracer link flags (`-I<dftracer include> -L<dftracer lib>
  -Wl,-rpath,<dftracer lib> -ldftracer_core`) must be added explicitly to the
  generated `vpic` wrapper script too — VPIC's own CMake has no dftracer
  awareness.
- VPIC's real I/O layer is `src/util/checkpt/checkpt_io.cc` (thin wrapper over
  `CheckPtIO`), not `FileIO.h` (a header-only template — correctly skipped by
  the annotation cost filter). Main simulation loop is
  `vpic_simulation::advance()` in `src/vpic/advance.cc`, called from
  `deck/main.cc`'s `while (simulation->advance())`.

## Annotation

- `DFTRACER_CPP_FUNCTION()` is RAII-scoped — there is no C++ END macro; only
  `DFTRACER_CPP_FUNCTION()` + `DFTRACER_CPP_FUNCTION_UPDATE("comp", ...)` at
  the top of every non-`main` function.
- `main()` uses `DFTRACER_CPP_INIT` + `DFTRACER_CPP_REGION_START(main)` at
  entry and `DFTRACER_CPP_REGION_END(main)` + `DFTRACER_CPP_FINI()` before the
  single `return`.
- **`DFTRACER_CPP_REGION_START`/`END` take a BARE IDENTIFIER, not a string
  literal.** The macro expands via `##name` token-pasting
  (`DFTracer* profiler_##name = ...` / `delete profiler_##name`), so
  `DFTRACER_CPP_REGION_START("main")` fails to compile
  (`expected ';' after expression`) — it must be
  `DFTRACER_CPP_REGION_START(main)`.
- **A `comp=` annotation label based on a function's NAME is not reliable
  evidence of what it actually does.** `dump_energies` was annotated
  `comp="io"` (name suggests a dump/I/O routine) but is actually 99.7%
  `MPI_Allreduce` by wall time — a communication routine. See
  `[[dftracer-diagnoser]]` pitfalls for the general rule this produced.

## Deck sizing — smoke vs validation

- VPIC's `TestScripts/data/portability_test*.cxx` decks (including the
  smallest-named `portability_test_x2-y2-z2.cxx`) are FULL PRODUCTION SCALE —
  ~80M particles/rank, 5302 timesteps. They are NOT smoke-test decks despite
  living under `TestScripts/`; they take far longer than a short pdebug
  allocation and their name only encodes the MPI domain-decomposition shape
  (`x2-y2-z2` = 2×2×2 = 8 ranks required), not problem size.
- `sample/Weibel/Weibel.cxx` is a genuinely small deck: `nppc=200`,
  `nx=64, ny=nz=1` → only 12,800 total macro particles, `num_step=1000`, and
  `topology_x = nproc()` auto-adapts to whatever rank count you launch with
  (no fixed decomposition requirement). This is the right deck for a smoke
  test / quick best-case trace — it completes in well under a minute at
  4-16 ranks on a single node.
- Any `portability_test_x<N>-y<M>-z<K>.cxx` deck REQUIRES exactly
  `N*M*K` MPI ranks (its domain decomposition is baked into the deck) — using
  the wrong rank count fails immediately with
  `Bad domain decompostion (NxMxK)` at `src/grid/partition.cc`.

## Measured diagnosis (Tuolumne, 16 ranks/1 node, Weibel deck, best_case trace)

VPIC is **MPI-communication-bound**, not I/O-bound: 82% of the per-timestep
`advance()` loop's wall time is `MPI_Allreduce` (the `dump_energies` global
energy-diagnostic reduction, 1.65s/3008 calls) unioned with
`boundary_p_kokkos` (particle/halo boundary exchange, MPI_Wait-dominated).
Pure Kokkos compute inside `advance()` (excluding both of those) is only
~18% of the loop. POSIX/STDIO I/O is negligible (<0.2% of total time — no
real file reads/writes in this configuration, just `access`/`fopen`-class
metadata calls). Optimization should target reducing/overlapping the
`MPI_Allreduce` cost (e.g. non-blocking `MPI_Iallreduce`, less frequent
energy-diagnostic cadence traded against physics-validity, or reduced
message/wait imbalance in the boundary exchange) — not I/O tuning, and not
"do less work" moves that reduce the diagnostic's frequency without
justifying it against the physics requirement (see
`feedback-app-pattern-swap-not-optimization`).

## Optimization candidates (STEP 8 findings, not yet measured)

Root cause of the `MPI_Allreduce` cost: `dump_energies` (`src/vpic/dump.cc:41`)
issues THREE separate small blocking `mp_allsum_d` reductions per timestep —
one 6-double field-energy reduction (`energy_f_kokkos`,
`src/field_advance/standard/energy_f.cc:207`) and one 1-double-per-species
reduction (`energy_p_kokkos`, `src/species_advance/standard/energy_p.cc:208`).
Three separate small-message blocking collectives per step is sync-bound
(latency-dominated, not bandwidth-dominated).

**PATCH #1 (primary, highest confidence):** coalesce all three into ONE
`MPI_Allreduce` over a packed `[6 + Nspecies]`-double buffer. Physics-identical
(linear MPI_SUM is associative over the concatenated buffer) — this is a pure
message-aggregation fix, cite Thakur & Gropp, IJHPCA 2005 (collective
communication optimization). Both `energy_f_kokkos`/`energy_p_kokkos` are
library code (`libvpic.a`), not deck code — applying this requires an
incremental relink of the app library (Kokkos objects are unaffected, so a
relink is ~2-5 min, not a full rebuild), not just recompiling a deck.

**PATCH #1, species subset — IMPLEMENTED AND VERIFIED (2026-07-14):** the
species-only half of PATCH #1 was actually applied: added
`energy_p_local()` (`src/species_advance/standard/energy_p.cc`, declared in
`species_advance.h`) — `energy_p_kokkos()` minus its internal
`mp_allsum_d` — and changed `dump_energies()` to call it per species,
accumulate into one buffer, and issue ONE `mp_allsum_d` for all species
instead of one per species. `energy_f_kokkos` (field energy) was
deliberately left untouched: it's a `CHECKPT_SYM`/`RESTORE_SYM`-registered
function pointer with a second runtime-selected variant
(`vacuum_energy_f_kokkos`), so coalescing it safely needs to touch
`field_advance.h`/`.cc`, `sfa.cc`, `sfa_private.h` too — bigger blast
radius, not attempted under this session's time budget. Rebuild is a
`make vpic` in `build_ann/` (12s, only ~8 files recompile) + a deck relink
(~10s) — genuinely cheap once you're set up.

Verified via full-file `diff` of `rundata/energies` between pre- and
post-patch 128-rank runs: **byte-identical output at every timestep**
(cvac^2 scaling commutes with summation across species, as expected) — the
patch is physics-preserving. Measured wall-time delta on a 2-species
(`I2`, `electron`) benchmark.cxx deck: none measurable (124.29s vs.
124.03s) — removing 1 extra blocking call out of ~1000 timesteps is below
the noise floor at this species count. Expect a real time win only for
decks with many species or much higher `energies_interval` frequency.
**PATCH #1's field-energy half + PATCH #2 (`MPI_Iallreduce`) remain
unapplied** — still the highest-confidence *un-applied* lever for a
future session with more time.

**PATCH #2 (stacks on #1):** switch to non-blocking `MPI_Iallreduce` for the
coalesced energy reduction, since the result is only consumed by rank-0's
diagnostic printout — it is off the critical path and can overlap with the
next timestep's compute. Cite Hoefler et al., SC 2007 (nonblocking collective
communication).

**Secondary, cheap, no-rebuild:** VPIC's own run scripts never set
`OMP_NUM_THREADS`/`OMP_PROC_BIND`/`OMP_PLACES` even though the build is
Kokkos-OpenMP — verify actual thread count/affinity at runtime before assuming
all cores are used; this is a pure environment-variable check, test it
alongside any PATCH #1/#2 validation since it's nearly free to include.

**Explicitly NOT worth pursuing for this workload:** pinned-memory/cross-NUMA
placement (MI300A's unified CPU+GPU HBM domain makes these structurally
inert), and launcher-level CPU affinity (`flux cpu-affinity` confirmed to
have no measurable effect on this system, twice, across different
workloads — tune via `OMP_PLACES` inside the process instead). I/O/ROMIO
tuning is now measured-not-worth-pursuing too — see below.

## `sample/benchmark.cxx` has I/O compiled out by default — check for this pattern

`sample/benchmark.cxx:18` hardcodes `#define ENABLE_OUTPUT 0`, which wraps
the ENTIRE diagnostics/dump block (`dump_mkdir`, `dump_energies`, field/
particle/hydro dumps — everything between the `#if ENABLE_OUTPUT` at
line 1059 and its matching `#endif`) out of the binary at compile time.
A trace of this binary will show 0.000 MB/s I/O bandwidth and an empty
run directory (just the binary + `params.txt`) — this is NOT a
dftracer/analyzer misconfiguration, it's the deck's own intentional
design (isolate compute+communication from I/O noise; the file is named
"benchmark" for a reason). Before concluding "this workload has no I/O,"
grep any deck for an `ENABLE_OUTPUT`-style compile-time gate. To measure
real I/O behavior, flip the macro to `1` and relink the deck — this is an
incremental compile of just `main.cc`+`wrapper.cc`+the deck against the
already-built `libvpic.a`, ~1 minute, not a full rebuild.

**Measured I/O profile with `ENABLE_OUTPUT=1`** (128 ranks/8 nodes,
`benchmark.cxx`, dumps at T.0/T.500/T.1000 → `field/`, `particle/`,
`ehydro/`, `I1hydro/`, `I2hydro/`, `restart/`, `rundata/energies`, 1.8 GB
total on Lustre): 340.85M POSIX ops for only 325.0 MB transferred
(196.9 MB/s aggregate bandwidth), but only **1.65s of actual I/O time out
of 210.2s job time** (<1% of wall time). Average transfer size is a small
fraction of a byte per op — a pronounced small-I/O anti-pattern (VPIC's
dump routines write per-cell/per-particle rather than batching), but
because total I/O time is so small relative to the MPI-communication cost
(82% of the timestep loop, see above), this workload's I/O really is
negligible next to communication — confirms and supersedes the earlier
"untested, no real file I/O" caveat from the Weibel-deck-only diagnosis.
**Verdict: I/O/ROMIO/Lustre-striping levers are NOT worth pursuing for
this workload** (now measured, not just inferred from a no-I/O deck) — if
I/O ever mattered at larger scale, the lever would be write-coalescing
(batch per-cell/per-particle writes into fewer, larger ones), not
ROMIO/striping tuning, since the bottleneck shape is op-count-bound, not
bandwidth-bound.
