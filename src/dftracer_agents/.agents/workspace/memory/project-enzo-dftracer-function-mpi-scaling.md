---
name: project-enzo-dftracer-function-mpi-scaling
description: Enzo 2.6.1 annotate + function/MPI dftracer runs on Tuolumne — 16-3 1Nx32 full run traced (358M events); 32-7 2Nx32 partial; 4N/8N and compaction pending
metadata:
  type: project
---

Enzo (enzo-dev tag enzo-2.6.1) with ComputationalUncertaintyHPC configs, dftracer FUNCTION mode, call-stack (CPP_APP) + MPI only (POSIX/STDIO disabled). Knowledge lives in [[workload-enzo]] skill.

State at end of session:
- dftracer = PyPI prerelease sdist with MPI on (PAPI/HIP/variorum/HDF5 off); app built first, HDF5 1.10.11 from source.
- 985/1061 .C files annotated (~1430 fns); main INIT, FINI inside my_exit(); pre-main ctors (ActiveParticleType, StochasticForcing) excluded.
- 16-3 @ 1N x 32: full z=99->0, 560 s, 358M events (324M cpp_app, 26.6M p2p, 1.4M collective). Compaction attempt killed when the allocation expired; raw traces intact.
- 16-3 @ 4N x 64: NaN interpolation abort at z~18.9 (over-decomposition suspected, unproven).
- 32-7 @ 2N x 32: capped at 12 min (z~5.35), 585M events.
- Pending: re-run compaction; 4N and 8N x 32 runs of 32-7 (8-node pdebug alloc was queued).

**Why:** the user wants multi-node (2/4/8) Enzo traces with many call-stack + MPI events.
**How to apply:** resume with scripts/run_scaled.sh <N> <PPN> 32-7-10Mpc-z0 <time>; give 32-7 runs >=25 min or add StopCycle for comparable lengths. See [[feedback-always-function-mode]], [[bug-dftracer-stats-categories-zero-events]].
