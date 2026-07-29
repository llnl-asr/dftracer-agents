---
name: system-matrix
description: Matrix — LC CZ GPU cluster (Intel Xeon Platinum 8480+, 112 cores + 4 GPUs/node, ~503 GB RAM, small ~35-node pbatch), Slurm-scheduled, ROCm 6.4.3/7.2.0. Load with site-lc for any build, allocation, or run on matrix.
---

# System: Matrix

Intel-CPU + GPU cluster in the LC **CZ** zone. Small but fat-memory nodes. Load
[[site-lc]] first.

> **Verified 2026-07-28** by read-only query from a matrix login node. Values marked
> *(login node)* were observed there; login nodes carry no GPUs.

## Hardware

| | |
|---|---|
| CPU | Intel Xeon Platinum 8480+ (Sapphire Rapids), 2 sockets, 56 cores/socket, 8 NUMA domains |
| Cores per compute node | **112** (`sinfo -o "%c"`) |
| Memory per node | **~503 GB** (`sinfo` reports 515111 MB) — roughly 2× [[system-dane]] |
| GPUs | **4 per node** — `sinfo -o "%G"` reports `gpu:4(S:0-1)`, i.e. 2 per socket |
| Interconnect | Mellanox InfiniBand — `mlx5_2`, `mlx5_3`, `mlx5_bond_0` |
| OS | TOSS 4.8-25 / RHEL 8.10 |

GPUs are **AMD**: `/opt/rocm-6.4.3` and `/opt/rocm-7.2.0` are installed and
`nvidia-smi` fails to find a driver. **TODO:** exact GPU model — confirm on a
compute node with `rocminfo` / `rocm-smi --showproductname`.

The 4-GPU/node, ~503 GB layout makes matrix the closest small-scale analogue to a
[[system-tuolumne]] node (4 GPUs/node) for DDP work that does not need MI300A APUs.

## Scheduler — Slurm

`sbatch`/`srun` plus `flux` 0.86.0 installed, but the live scheduler is **Slurm**
(`flux queue list` returns nothing; `sinfo` works). Charge with `-A <bank>`.

**Verified partitions** (`sinfo -s`):

| Partition | Time limit | Nodes |
|---|---|---|
| `pbatch` (default) | 1 d | 35 (`matrix[11-30,37-51]`) |
| `pci` | 1 d | 20 (`matrix[11-30]`) |
| `pdebug` | 1 h | 2 (`matrix[9-10]`) |

This is a **small** machine — `pdebug` is 2 nodes and `pbatch` 35, and at the time
of survey `pbatch` was fully allocated (35/0/0/35). Plan scale accordingly; do not
assume Tuolumne-sized capacity.

## Toolchain — NOT Cray PE

- **Compilers:** `intel/*` and `intel/*-magic` through 2025.3.1; default loaded is
  `intel-classic/2021.6.0-magic`. Prefer `-magic`.
- **MPI:** `mvapich2/2.3.6`, `mvapich2/2.3.7` (default), `openmpi/4.1.2`.
- **Python:** `python/3.9.12` … `3.13.2`.
- **ROCm:** `/opt/rocm-6.4.3`, `/opt/rocm-7.2.0`. Apply the [[software-rocm]]
  selection rule and check `module avail rocm` for patched variants; put
  `$ROCM_PATH/bin` on PATH so `rocminfo` resolves.
- No `PrgEnv-cray`/`cce`/`cray-mpich`.

## Filesystems

`/p/lustre1`, `/p/lustre2`, `/p/lustre5`, `/p/vast1`. Note this differs from
[[system-corona]]/[[system-dane]] (which have `lustre3` but no `lustre5`) — never
assume a Lustre mount exists across LC machines.
