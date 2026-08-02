---
name: feedback-torchrun-hpc-flags
description: torchrun-hpc -n flag is procs-per-node NOT total procs; for 8 nodes × 4 GPUs use -N 8 -n 4 --gpus-per-proc 1
metadata: 
  node_type: memory
  type: feedback
---

**Canonical home:** see the `workload-scaffold` skill (baseline/smoke-test run-shape
sections already apply this flag convention throughout).

`torchrun-hpc -n` means **procs per node**, not total process count. Using `-N 8 -n 32` requests 32 processes per node (256 total), which fails with "alloc denied due to type=unsatisfiable" on Tuolumne.

**Why:** Unlike `mpirun -n` (total ranks), torchrun-hpc follows the `--procs-per-node` convention.

**How to apply:** On Tuolumne MI300A with 4 GPUs/node:
- 1 node:  `torchrun-hpc -N 1 -n 4 --gpus-per-proc 1`
- 8 nodes: `torchrun-hpc -N 8 -n 4 --gpus-per-proc 1`

General formula: `-n` = GPUs per node (4 on Tuolumne).
