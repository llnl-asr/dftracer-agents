---
name: system-corona
description: Corona — LC CZ AMD-GPU cluster (AMD EPYC CPUs + 8 AMD GPUs/node), Flux-native scheduler, ROCm module stack up to 7.2.x, mvapich2/openmpi (NOT Cray PE). Load with site-lc for any build, allocation, or run on corona.
---

# System: Corona

AMD CPU + AMD GPU cluster in the LC **CZ** zone. Load [[site-lc]] first for
banks/`bankinfo`, Flux usage, Lmod conventions, and filesystem rules.

> **Verified 2026-07-28** by read-only query from a corona login node. Values marked
> *(login node)* were observed on the login node — compute nodes differ, most
> importantly in GPUs, which login nodes do not have.

## Hardware

| | |
|---|---|
| CPU | AMD EPYC 7401 24-Core, 2 sockets, 8 NUMA domains, 96 logical CPUs *(login node)* |
| Memory | ~251 GB *(login node)* |
| GPUs | **8 AMD GPUs per node** — derived from `flux queue list`: `pbatch` 104 nodes / 832 GPUs, `pdebug` 15 / 120 |
| Interconnect | Mellanox InfiniBand — `mlx5_2`, `mlx5_3`, `mlx5_bond_0` (`ibstat -l`) |
| OS | TOSS 4.8-25 / RHEL 8.10 |

**TODO:** exact GPU model (MI50/MI60-class expected but NOT verified) and per-node
core count. Confirm from a compute node with `rocminfo` / `rocm-smi --showproductname`.

## Scheduler — Flux native

`flux` 0.86.0 is present; there is **no `sbatch`/`srun`** on corona (unlike
[[system-dane]]/[[system-matrix]]). Use native Flux, or `module load flux_wrappers`
for the Slurm-compatible shims.

**Verified queues** (`flux queue list`):

| Queue | Default / limit | Nodes | GPUs |
|---|---|---|---|
| `pbatch` (default) | 30 m / 1 d | 0–104 | 0–832 |
| `pdebug` | 30 m / 1 h | 0–15 | 0–120 |

Note `pbatch`'s **default is only 30 m** here (on [[system-tuolumne]] it is 12 h) —
always pass `-t` explicitly.

## Toolchain — NOT Cray PE

Unlike Tuolumne, corona has **no `PrgEnv-cray`/`cce`/`cray-mpich`**. Do not copy a
Tuolumne module block here; it will fail.

- **Compilers:** `intel/*` and `intel/*-magic` (through 2025.3.1); default loaded
  environment is `intel-classic/2021.6.0-magic`. Prefer `-magic` variants.
- **MPI:** `mvapich2/2.3.6`, `mvapich2/2.3.7` (2.3.7 is the loaded default),
  `openmpi/4.1.2`.
- **ROCm:** wide range through `rocm/7.2.1` (`5.7.x`, `6.0.x`, `6.1.0`, `6.4.2`,
  `6.4.3`, `7.1.0`, `7.2.0`, `7.2.1`). `/opt/rocm-*` trees exist back to 4.2.0.
  Apply the [[software-rocm]] selection rule — check `module avail rocm` for
  patched `leakfix`/`cgroupfix`/`hangfix` variants; the bare version is the lmod
  default and is unpatched.
- `rocminfo` is **not on PATH** until you load a rocm module and add
  `$ROCM_PATH/bin` — see [[software-rocm]] for why a missing `rocminfo` silently
  deadlocks multi-rank JIT builds.

Set `CC`/`CXX` from the loaded MPI wrappers (`which mpicc`), never hardcoded —
see `feedback-cc-cxx-mpi-selection`.

## Filesystems

`/p/lustre1`, `/p/lustre2`, `/p/lustre3`, `/p/vast1`. **No `/p/lustre5`** — a path
that works on Tuolumne or Matrix may not exist here. Application data goes on the
PFS; see [[site-lc]].
