---
name: project-vpic-kokkos-8node-validation
description: VPIC-Kokkos annotate/optimize/8-node-validate session on Tuolumne — 41% measured speedup from OMP threading fix, plus a verified correctness-preserving MPI_Allreduce coalescing patch
metadata:
  type: project
---

Completed a full annotate → trace → diagnose → optimize → 8-node validate
pipeline on vpic-kokkos (github.com/lanl/vpic-kokkos) on Tuolumne, and this
time went further: applied and measured two real optimizations rather than
just documenting candidates.

**Outcome:** VPIC is confirmed MPI-communication-bound, not I/O-bound, at
both 16-rank (Weibel deck) and 128-rank/8-node (`sample/benchmark.cxx`)
scale. Real I/O was also measured (after fixing `benchmark.cxx`'s
`ENABLE_OUTPUT=0` compile gate — see [[workload-vpic-kokkos]]): negligible,
op-count-bound, <1% of wall time.

**Applied optimization #1 — OMP threading (dominant, measured 41% faster,
210.2s → 124.0s):** VPIC's Kokkos-OpenMP build ran 1 thread/rank by
default (6 idle cores/rank). Fixed via `flux run -c6` +
`OMP_NUM_THREADS=6 OMP_PROC_BIND=spread OMP_PLACES=cores` — pure runtime
env change, no rebuild. See [[dftracer-compute-optimization]].

**Applied optimization #2 — species-energy MPI_Allreduce coalescing
(correctness-verified, no measurable time win at this scale):** added
`energy_p_local()` to remove per-species blocking reductions in
`dump_energies`, coalescing N species into 1 `mp_allsum_d` call. Verified
via full-file `diff` of `rundata/energies`: byte-identical output
pre/post-patch. No measurable wall-time delta with only 2 species — would
matter more for many-species decks. Field-energy coalescing
(`energy_f_kokkos`, `CHECKPT_SYM`-registered, has a `vacuum_energy_f_kokkos`
variant) deliberately left unapplied — bigger blast radius, still the
highest-confidence *un-applied* lever.

**Why:** requested to actually improve app time / system utilization, not
just diagnose and propose — this session delivered a real, measured,
41%-faster result plus a verified-safe (if small) correctness improvement,
all validated end-to-end within a single ~60-min pdebug allocation window.

**How to apply:** future vpic-kokkos sessions (or any Kokkos-OpenMP app on
this system) should check `OMP_NUM_THREADS`/`OMP_PROC_BIND`/`OMP_PLACES`
and launcher core-per-task binding FIRST — it's free, no-rebuild, and was
the single biggest lever found this session, bigger than any MPI-side fix
attempted. See [[workload-vpic-kokkos]], [[dftracer-compute-optimization]],
[[dftracer-communication-optimization]] for full details.
