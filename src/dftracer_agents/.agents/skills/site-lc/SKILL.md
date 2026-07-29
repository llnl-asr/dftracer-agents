---
name: site-lc
description: Livermore Computing (LC) — the site-level facts shared by EVERY LC cluster: zones (CZ/RZ/SCF), bank/account charging (bankinfo, flux -B, slurm -A), the Flux and Slurm schedulers and LC's flux_wrappers Slurm-compatibility layer, the Lmod module system and TOSS OS, and the shared filesystem layout. Load this skill before any job submission, allocation, or environment setup on an LC machine, then load the matching system-<cluster> skill for the machine-specific details.
---

# site-lc — Livermore Computing

A **site** is an organization that operates several clusters; a **system** is one
cluster at that site. Facts true of every LC machine live here. Facts true of one
machine live in `system-<name>`.

LC systems covered by their own skills: [[system-tuolumne]], [[system-corona]],
[[system-dane]], [[system-matrix]].

> Verification note: everything marked **verified** below was confirmed by running
> the command shown on an LC CZ login node on 2026-07-28. Items marked **TODO** are
> not yet confirmed — do not present them as fact.

## Zones

LC is partitioned into security zones. A cluster belongs to exactly one, and they
are network-isolated from each other — you cannot reach an RZ or SCF host from CZ.

| Zone | Name | Notes |
|---|---|---|
| CZ | Collaboration Zone | Least restricted; open collaboration. Most of the systems this project uses. |
| RZ | Restricted Zone | Tighter access controls. |
| SCF | Secure Computing Facility | Classified. |

**Verified:** the CZ workspace mounts as `cz-ws4-nfs-new.llnl.gov:/cz_workspace`
on `/usr/workspace` (`df -hT /usr/workspace`) — the `cz_` prefix is a reliable
zone tell.

## Banks (job charging) — REQUIRED on most queues

A **bank** is the account a job's usage is charged against. Banks form a
hierarchical tree with fair-share weighting: each node has *shares*, and a bank's
scheduling priority decays as its *usage* grows relative to those shares.

**Verified — discover your banks:**
```bash
bankinfo            # from flux_wrappers; prints your default bank + the share tree
```
Output shape (anonymized):
```
Default bank is <bank>
Name          Shares  Norm Shares  Norm Usage  Level FS
ROOT               1     0.000000    0.000000     --
 <parent-bank> 128100     0.128024    0.168243     --
  <bank>*         542     0.000542    0.014681     --
    <user>          1     0.000001    0.011247   0.317
```
- `*` marks your default bank.
- `Norm Usage` climbing toward `Norm Shares` means your priority is falling.
- `Level FS` is the fair-share factor — lower means more recently-consumed usage.

**Verified — pass the bank to the scheduler:**
```bash
flux alloc -B <bank> -N4 -t 30m -q pbatch      # Flux: -B / --bank
sbatch  -A <bank> ...                          # Slurm: -A / --account
```
`flux alloc --help` confirms `-B, --bank=BANK`. If you omit it you get the
default bank from `bankinfo`, which is usually right — but be explicit in any
script that ships to another person, since their default differs.

Reference: <https://hpc.llnl.gov/banks-jobs/banks> (**not reachable from LC
compute/login hosts — times out**; read it from a workstation).

## Schedulers

LC runs **Flux** on newer systems and **Slurm** on others; some systems run Flux
with a Slurm-compatibility layer on top.

**Verified on a Flux system:** `flux version` → commands 0.86.0,
libflux-core 0.86.0, libflux-security 0.15.0.

```bash
flux queue list                 # queues, time limits, node/core/gpu ranges
flux resource list              # free / allocated / down, with nodelists
flux jobs -a                    # your jobs; --filter=RUN for live ones
flux alloc -N<n> -t <time> -q <queue> --bg   # background allocation, returns a jobid
flux proxy <jobid> flux run ... # run INSIDE an existing allocation
```

**A bare `flux run` queues a NEW job instead of using your allocation.** To use an
allocation you already hold, always go through `flux proxy <alloc-id>` first. This
has bitten this project repeatedly.

**Verified — LC ships Slurm-command wrappers for Flux** at
`/usr/global/tools/flux_wrappers/bin`, loaded via `module load flux_wrappers`:
`salloc sbatch srun squeue scancel sinfo sbcast` plus LC extras
`bankinfo checkjob jobinfo showq mshare fluxreport quickreport utilizationreport
slurm2flux suflux sxterm`. `slurm2flux` translates an existing Slurm script.
These are convenience shims — prefer native `flux` commands when writing new
scripts, and reach for the wrappers when porting Slurm scripts.

See [[flux-alloc]] for the full allocate-then-proxy workflow.

## OS and module system

**Verified:** RHEL 8.10 (Ootpa) under **TOSS** — LC's site Linux distribution
(`/etc/toss-release` → `toss-release-4.8-25`). Cray-PE systems additionally carry
`/etc/toss-cray-release`. Checking `/etc/toss-release` is the quickest way to
confirm you are on an LC machine at all.

**Verified:** modules are **Lmod** (Lua Modules 8.7.55).
```bash
module avail <name>     # ALWAYS run this before assuming a version exists
module help <mod>       # states what a patched/site variant actually changes
module purge            # start from a known state in scripts
```

Two LC module conventions that cause real failures:

- **`-magic` suffix** (on compiler modules such as `rocmcc/*`, `cce/*`) marks a
  site-blessed, tested pairing of compiler components. Prefer them.
- **Patched builds live beside the base version and the BASE ONE IS THE LMOD
  DEFAULT.** Suffixes like `leakfix`, `cgroupfix`, `hangfix` are separate installs
  (e.g. under `/usr/tce/packages/rocbeta/`), not aliases. `module load rocm/6.4.3`
  silently gives you the *unpatched* build. Always `module avail` and prefer a
  patched variant. See [[software-rocm]] for the full selection rule and the
  `ROCM_PATH`-derivation trap.

**Never hardcode a toolchain path.** Load the module and derive from the variable
it exports (`$ROCM_PATH`, `$HDF5_ROOT`, `which mpicc`). Hardcoding
`/opt/rocm-<ver>` while loading a patched module silently mixes two trees.

## Filesystems

| Path | Kind | Use |
|---|---|---|
| `/usr/workspace/<user>` | NFS | Home-ish workspace. Small quota, NOT for job I/O. **Verified** ~95 TB filesystem, NFS-mounted. |
| `/p/lustre<N>` | Lustre PFS | **Where application data belongs.** Datasets, checkpoints, run output. **Verified:** `/p/lustre5` present. |
| `/p/vast<N>` | VAST NVMe | Flash-backed PFS. **Verified:** `/p/vast1` present. |

**Rule:** application data always lives on the PFS, never in an NFS workspace.
dftracer *traces* are the exception — they stay in the session workspace. See
`feedback-lustre-io`.

For any multi-GB/TB or >10k-file copy/sync/delete/compare on a PFS, use
[[software-mpifileutils]] (`dcp`/`dsync`/`drm`/`dwalk`/`dstripe`), never serial
`cp`/`rsync`/`rm -rf`.

## oslic — the download / data-staging host

**Use `oslic` for downloading large packages, datasets, model weights, and
container images.** It is the LC host provisioned for external fetches; compute
clusters are not. Do not run multi-GB downloads from a compute cluster's login
node.

**Verified 2026-07-28** from `oslic`:
- External connectivity works: `github.com` → 200, `pypi.org` → 200.
- It mounts a **superset** of the parallel filesystems —
  `/p/lustre1`, `/p/lustre2`, `/p/lustre3`, `/p/lustre5`, `/p/vast1` — whereas
  individual clusters mount only some (corona/dane have `lustre3` but no
  `lustre5`; matrix has `lustre5` but no `lustre3`). That superset is the whole
  point: stage a dataset once on oslic, straight onto the Lustre path the target
  cluster can see.
- Hardware: Intel Xeon E5-2695 v4, 72 logical CPUs. It is a **staging host, not a
  compute resource** — download and unpack there, run elsewhere.

Workflow: `ssh oslic` → download into `/p/lustre<N>/$USER/...` (a filesystem the
target cluster mounts) → run the job on the compute cluster against that path.
For the subsequent large copy/move, use [[software-mpifileutils]], not `cp`.

**`hpc.llnl.gov` is unreachable even from oslic** (TCP timeout, verified). LC's own
documentation site cannot be fetched from inside LC — read it from a workstation
and paste the content in.

## Cross-cluster quick reference

Never assume one cluster's environment transfers to another. **Verified 2026-07-28:**

| | [[system-tuolumne]] | [[system-corona]] | [[system-dane]] | [[system-matrix]] |
|---|---|---|---|---|
| CPU | AMD (Cray) | AMD EPYC 7401 | Intel Xeon 8480+ | Intel Xeon 8480+ |
| GPUs/node | 4 (MI300A APU) | 8 (AMD) | **none** | 4 (AMD) |
| Cores/node | — | — | 112 | 112 |
| RAM/node | — | — | ~257 GB | ~503 GB |
| Scheduler | Flux | Flux | **Slurm** | **Slurm** |
| Toolchain | **Cray PE** (`cce`, `cray-mpich`) | intel + mvapich2/openmpi | intel + mvapich2/openmpi | intel + mvapich2/openmpi |
| Lustre | `lustre5` | `lustre1/2/3` | `lustre1/2/3` | `lustre1/2/5` |

Two traps this table exists to prevent:
1. **Cray PE is Tuolumne-only.** A `PrgEnv-cray`/`cce`/`cray-mpich` module block
   copied to corona/dane/matrix fails outright — they use `intel*-magic` +
   `mvapich2`/`openmpi`.
2. **Module visibility is not hardware.** Dane exposes `cuda/*` modules and has
   `nvidia-smi`, but has **zero GPUs**. Always confirm with the scheduler
   (`sinfo -o "%G"`, or Flux's `NGPUS` column), never with `module avail`.

## ML / DL stack

There is no site-wide blessed PyTorch. The working pattern is a per-session venv
layered on site modules — see [[software-dl-stack]] for the ordered, gated
install methodology, and [[software-rocm]] / [[software-rccl]] on AMD systems.
Key site-specific traps: `pip install torch` resolves to a **CUDA** wheel even on
AMD hardware, and PyPI wheels of torch-adjacent C-extension packages are not ABI
compatible with a custom ROCm torch.

## TODO — not yet verified

Gaps to close on the next `learn` pass; do not state these as fact until checked:

- Per-system hardware tables for corona / dane / matrix (node counts, CPU/GPU
  models, interconnect, scheduler, zone). `hpc.llnl.gov/hardware/compute-platforms`
  is the authoritative source but is **unreachable from LC hosts**.
- Whether corona / dane / matrix run Flux or Slurm natively.
- Default and maximum walltimes per queue on each system (varies per machine;
  on the verified Flux system: `pdebug` 30 m default / 1 h limit, `pbatch` 12 h /
  1 d — but that is a *system* fact, recorded in [[system-tuolumne]], not a site fact).
- Whether bank names/structure differ by zone.
