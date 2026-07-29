---
name: system-dane
description: Dane — LC CZ CPU-ONLY capacity cluster (Intel Xeon Platinum 8480+ Sapphire Rapids, 112 cores/node, ~1440 pbatch nodes), Slurm-scheduled. No GPUs despite cuda modules being visible. Load with site-lc for any build, allocation, or run on dane.
---

# System: Dane

Large Intel CPU capacity cluster in the LC **CZ** zone. Load [[site-lc]] first.

> **Verified 2026-07-28** by read-only query from a dane login node. Values marked
> *(login node)* were observed there.

## Hardware — CPU ONLY

| | |
|---|---|
| CPU | Intel Xeon Platinum 8480+ (Sapphire Rapids), 2 sockets, 56 cores/socket, 8 NUMA domains |
| Cores per compute node | **112** (`sinfo -o "%c"`) |
| Memory per node | ~257 GB (`sinfo` reports 257054 MB) |
| **GPUs** | **NONE** — `sinfo -o "%G"` reports `(null)` on every partition |
| Interconnect | `hfi1_0` (Omni-Path HFI) + `mlx5_0`, `mlx5_bond_0` |
| OS | TOSS 4.8-25 / RHEL 8.10 |

**Do not send GPU workloads here.** `cuda/12.6.0`, `cuda/12.9.1`, `cuda/13.1.1`
modules are *visible*, and `nvidia-smi` exists but fails with "couldn't communicate
with the NVIDIA driver". Module visibility is NOT evidence of hardware — the
authoritative check is the scheduler's GRES (`sinfo -o "%G"`). For GPU work use
[[system-corona]], [[system-matrix]], or [[system-tuolumne]].

## Scheduler — Slurm

Both `sbatch`/`srun` and `flux` 0.86.0 are installed, but the live scheduler is
**Slurm** — `flux queue list` returns nothing while `sinfo` works. Submit with
`sbatch`/`srun` and charge with `-A <bank>` (see [[site-lc]]).

**Verified partitions** (`sinfo -s`):

| Partition | Time limit | Nodes |
|---|---|---|
| `pbatch` (default) | 1 d | 1440 (`dane[105-1544]`) |
| `pci` | 1 d | 1440 |
| `pserial` | **7 d** | 16 (`dane[89-104]`) |
| `pdebug` | 1 h | 38 |
| `pjupyter` | 1 d | 2 |
| `pall` | infinite | 1496 (**down**) |

`pserial`'s 7-day limit is the longest available and is the right home for long
single-node serial work.

## Toolchain — NOT Cray PE

- **Compilers:** `intel/*` and `intel/*-magic` through 2025.3.1; default loaded is
  `intel-classic/2021.6.0-magic`. Prefer `-magic`.
- **MPI:** `mvapich2/2.3.6`, `mvapich2/2.3.7` (default), `openmpi/4.1.2`.
- **Python:** `python/3.9.12` … `3.13.2` (also 2.7.18).
- No `PrgEnv-cray`/`cce`/`cray-mpich` — do not reuse a Tuolumne module block.

Derive `CC`/`CXX` from `which mpicc` after loading modules.

## Filesystems

`/p/lustre1`, `/p/lustre2`, `/p/lustre3`, `/p/vast1`. **No `/p/lustre5`.**
