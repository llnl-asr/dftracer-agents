---
name: project-minife-genesis-matrix-sweep
description: miniFE genesis trace sweep on matrix (H100/Sapphire Rapids) — merged CUPTI+PAPI+variorum dftracer, CUDA variant annotated/built, 1176-run grid launched; N=1 running, N>=2 on pci
metadata:
  type: project
---

Genesis trace-collection sweep for miniFE on an NVIDIA H100 / Sapphire Rapids
Slurm cluster, run from an existing miniFE session workspace that is being used
CONCURRENTLY from another system. Everything for this system is isolated by a
per-system suffix: `venv_<sys>/`, `dftracer_src_<sys>/`,
`genesis_traces/<sys>/`, `artifacts/genesis_<sys>/`.

**Grid (the requested dimensions):** 49 unique miniFE.x argument strings taken
from `llnl/ice4hpc_data` `data/merged.txt` (column 3 == `miniFE.x`; 42 of the 49
are anisotropic, not just the 7 cubes) x node scale {1,2,4,8} x processes-per-node
{1,2,4} (GPU ladder, 4 GPUs/node) x 2 PAPI counter sets = **1176 runs**.
Layout: `genesis_traces/<sys>/input_<nx>x<ny>x<nz>/nodes_<N>/ppn_<P>/{raw,compacted}/`.

**dftracer build.** No single branch had everything: the CUPTI branch has
CUPTI+MPI but no PAPI/variorum, and `develop` has PAPI/variorum/service-telemetry
but no CUPTI. Merged them (12 conflicts, all additive except a trace-type enum
clash where both sides claimed value 11 -- resolved by renumbering CUDA to 14 so
develop's already-serialized 11/12/13 stay stable). The union merge silently
dropped one `endif()` and duplicated an MPI option block; both had to be repaired
by hand. Verify features from `ldd`/`nm` and the generated config header, never
from the configure log alone.

**What works vs what does not on this class of machine:** CUPTI, PAPI (exact),
and the service utilization collectors all work. **variorum does not** -- and is
worse than useless, because pinning the service to one core on a two-socket node
makes variorum's init `exit()` and kill the whole daemon. Details in
[[system-matrix]], [[software-cupti]], [[software-papi]], [[tools-dftracer]],
[[workload-minife]].

**Why:** the deliverable is a dimensioned corpus of traces (GPU timeline +
exact hardware counters + node utilization + GPU power) for every
input/scale/concurrency combination, not a single optimization result.

**How to apply:** the sweep is checkpointed per verified run and fully
resumable -- re-running the driver picks up where it left off. Never checkpoint
on an exit code: several failures here returned rc=0 while writing no trace at
all. Verify artifacts on disk (per-rank app traces, per-node service traces,
power CSVs) before recording a run as complete.

Related: [[feedback-flux-allocation-vs-job]],
[[feedback-dftracer-service-node-counters]], [[feedback-always-function-mode]].
