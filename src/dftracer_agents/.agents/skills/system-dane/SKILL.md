---
name: system-dane
description: >
  System profile for dane (LLNL CTS-2 Intel Sapphire Rapids, TOSS 4, Slurm) —
  the CPU-ONLY machine of the set. What it shares with matrix (same CPU, same
  PAPI ceiling, same dead power story) and what it does not (no GPU at all, a
  1440-node pool, a genuinely multi-node pdebug, a 7-day pserial, no
  /p/lustre5). Includes the two false-positive GPU signals that make probe code
  claim hardware that is not there, and the bank check that gates every job.
  Load this skill for any dftracer session, build, or job launch on dane.
---

Cross-references: [[slurm-alloc]] [[system-matrix]] [[software-papi]] [[tools-dftracer]] [[genesis_run]] [[system-tuolumne]] [[system-tioga]]

dane is LLNL's CTS-2 commodity cluster: **1500 nodes of pure CPU**. It is the
odd one out among the machines these skills cover — [[system-tuolumne]] (MI300A),
[[system-tioga]] (MI250X) and [[system-matrix]] (H100) all have GPUs; dane has
none at all.

**The most useful mental model: dane is matrix with the GPUs removed and the
node pool multiplied by 40.** Same Xeon 8480+, same TOSS 4, same Slurm, same
PAPI ceiling, same unreadable power. So §5 (PAPI) and §6 (power) of
[[system-matrix]] transfer almost verbatim, while §2 (partitions) and §4 (CUDA)
do not transfer at all.

---

## 0. Before anything else — do you have a bank here?

An LC account on tuolumne/matrix/tioga does **not** imply a Slurm association on
dane. Without one, *every* submission fails — including the supposedly harmless
`--test-only` dry run, on every partition, with every explicit `-A`:

```
allocation failure: Invalid account or account/partition combination specified
```

The message names neither the bank nor the cluster, so it reads like a
partition problem. Check first, and stop rather than debugging partitions:

```bash
sacctmgr -nP show assoc user=$USER cluster=dane format=Account,Partition,QOS
sshare -U -u $USER          # empty table => no association
```

Empty output means no bank. Nothing in the rest of this skill can be exercised
until one is granted; request it from the coordinator for the relevant project.

---

## 1. Hardware — there is no GPU

Per compute node:

| | |
|---|---|
| GPUs | **none** |
| CPU | 2 × **Intel Xeon Platinum 8480+** (Sapphire Rapids), 3.8 GHz max |
| cores | **112 online** (56/socket, SMT off), 8 NUMA regions (SNC-4 per socket) |
| cache | 107 MB L3 |
| memory | ~251 GB |
| OS | RHEL 8.10 / TOSS 4 (`toss-release-4.8`) |
| scheduler | **Slurm** 25.11.7 |

> **Two false-positive GPU signals will lie to you here.** Probe code that keys
> on file existence rather than on a working device reports GPUs on dane:
>
> * `/usr/bin/nvidia-smi` **exists** (it is in the TOSS image) and fails at run
>   time with *"couldn't communicate with the NVIDIA driver"*.
> * **Eight** `/opt/rocm-*` trees exist (5.4.3 … 5.7.1, 6.4.3, 7.2.0). The 5.x
>   ones are `llvm/`-only stubs, but **6.4.3 and 7.2.0 are complete installs**
>   with `bin/`, `lib/`, `include/` and `amdgcn/` — a full ROCm userspace on a
>   machine with no AMD device. Anything that keys on `/opt/rocm*` existing will
>   confidently report an AMD GPU here.
>
> `lspci` shows no display or 3D controller. This is exactly why
> `system_detect`'s power probe still reports `gpu: rocm_smi (/opt/rocm-7.2.0)`
> on dane — `systems.yaml` pins `tracing.power: off` to override it. **Never set
> `DFTRACER_ENABLE_HIP_TRACING` or `DFTRACER_ENABLE_CUDA_TRACING` on dane**, and
> treat any tool that reports a GPU vendor here as having matched a path, not
> hardware. This is the same class of bug as matrix's `probe_power()` finding
> `rocm_smi` on an NVIDIA-only machine.

Also note `lscpu` reports **`CPU(s): 224`** while `On-line CPU(s) list: 0-111`.
`nproc` = **112** is the real number; 224 counts offline SMT siblings. Sizing a
ppn ladder off 224 over-subscribes every node by 2×. PAPI separately reports
`Sockets: 4` — it is counting SNC domains, not packages; `lscpu` says 2.

---

## 2. Partitions — a very different shape from matrix

`MaxNodes` is invisible in default `sinfo`; always `scontrol` it.

| partition | nodes | MaxNodes | MaxTime | observed backlog |
|---|---|---|---|---|
| `pbatch` (default) | 1440 | **520** | 24 h | **~5285 queued** |
| `pci` (QoS `ci_dane`) | 1440 | **520** | 24 h | **~7 queued** |
| `pdebug` | 38 | **20** | 1 h | ~36 queued |
| `pserial` | 16 | **1** | **7 days** | ~1546 queued |
| `pjupyter` | 2 | 1 | 24 h | — |
| `pall` | 1496 | unlimited | unlimited | **State=DOWN — never target it** |

Three things here differ from [[system-matrix]] and will burn a plan copied
across:

* **`pdebug` is genuinely multi-node** (`MaxNodes=20`), unlike matrix's
  `MaxNodes=1`. A 1→2→4→8→16 node ladder fits entirely inside pdebug's 1-hour
  limit, which is by far the fastest path for short per-run sweeps.
* **`pserial` is the only long-walltime option** — 7 days, but capped at
  **1 node**. Use it for long single-node work, never for scaling.
* **`pbatch` and `pci` are the same 1440-node pool** at the same MaxNodes and
  MaxTime, but the queue depth differs by three orders of magnitude. **Prefer
  `pci`.** As on matrix, a pending `pci` job reporting *"reserved for jobs in
  higher priority partitions"* is ordinary contention on shared hardware, not a
  broken node.

Confirm shape with the scheduler rather than intuition (needs a bank, §0):

```bash
for p in pdebug pci pbatch; do
  printf "%-8s " $p
  sbatch -p $p -N8 -t 60 --test-only --wrap=hostname 2>&1 | tail -1
done
```

Full allocation workflow, `--overlap`, `--nodelist` pinning and the daemon
lifetime rules: **[[slurm-alloc]]**.

---

## 3. Toolchain

The site default with no modules loaded is
**`intel-classic/2021.6.0-magic` + `mvapich2/2.3.7`**.

For dftracer work use the same gcc pairing as matrix, minus CUDA:

```bash
module load gcc/12.1.1-magic
module load mvapich2/2.3.7          # auto-swaps to the gcc-12.1.1 build
export CC=$(command -v mpicc) CXX=$(command -v mpicxx)
```

Available: `gcc` 10.3.1–13.3.1, `clang` 14–22, `intel`/`intel-classic`
19.0–2025.3, MPI = `mvapich2` 2.3.6/2.3.7 or `openmpi/4.1.2` (per-compiler trees
under `/usr/tce/modulefiles/MPI/<compiler>/`). Also present: `cmake` up to 3.30.5,
`meson`, `ninja`, `papi/6.0.0.1` (do **not** use — §4), `mpifileutils/0.12`,
`patchelf`, `python` 3.9–3.14, `hpctoolkit`, `vtune`, `valgrind`.

> **Do not copy matrix's module-rollback warning to dane.** Verified here:
> `module purge && module load ...` inside `bash -lc` **survives**, and the
> resulting `mpicc` resolves correctly. The `bash -lc` rollback is a
> matrix-specific trap, not a TOSS-wide one. Verifying with `which` is still
> good practice; asserting the rollback happens here is wrong.

CUDA modules (`cuda/10.1` … `13.1.1`) and Nsight are installed on dane, as is
`nvidia/375`. **They are toolchain packages on a machine with no GPU** — usable
to compile, never to run.

---

## 4. PAPI — 17 presets, and the module is useless

Identical to matrix, because it is the same CPU. Use the **system** PAPI:

| | |
|---|---|
| library | `/usr/lib64/libpapi.so.5` → **5.6.0.0** (LLNL-patched) |
| tools | `/usr/bin/papi_avail`, `papi_component_avail`, … |
| hardware counters | **19** |
| presets | **17** — 8 native, 9 derived |
| component | `perf_event` (PMUs `ix86arch, perf, perf_raw, spr`) |

The 17 presets, with the derived ones marked (a derived preset costs 2+ of the
19-counter budget, so sizing a set by counting names under-sizes it):

```
native  : PAPI_TOT_INS PAPI_TOT_CYC PAPI_REF_CYC PAPI_BR_INS
          PAPI_BR_CN PAPI_BR_TKN PAPI_BR_NTK PAPI_BR_MSP
derived : PAPI_BR_UCN PAPI_BR_PRC PAPI_FP_INS PAPI_VEC_INS
          PAPI_FP_OPS PAPI_SP_OPS PAPI_DP_OPS PAPI_VEC_SP PAPI_VEC_DP
```

> **The `papi/6.0.0.1` module reports 0 hardware counters and 0 presets on this
> CPU** — every preset shows `Avail: No`. It predates Sapphire Rapids. Loading
> it does not fail; it just silently yields a counter plan with nothing in it.
> `systems.yaml` therefore pins `tracing.papi_module: ""` for dane.

Disabled components, both expected:

* `rapl` — *"CPU model not supported"*
* `perf_event_uncore` — needs `perf_event_paranoid=0`; it is **1** here

Note the counter set is CPU-wide but **not the whole story on a 112-core,
8-NUMA node**: per-process `perf_event` counters work, uncore/memory-controller
counters do not. Full partitioning method and the silent counter-drop trap:
**[[software-papi]]**.

---

## 5. Power — nothing is readable, at all

| source | status |
|---|---|
| variorum / MSR | ❌ `/dev/cpu/*/msr` is `crw------- root root`; **`msr_safe` not loaded** |
| RAPL sysfs | ❌ `/sys/class/powercap/intel-rapl*/energy_uj` is mode **0400**, root-owned |
| PAPI `rapl` component | ❌ disabled, *"CPU model not supported"* |
| GPU power (NVML / rocm-smi) | ❌ **no GPU to sample** |

matrix at least keeps NVML as a fallback. **dane has no power source whatsoever
for an ordinary user.** `systems.yaml` sets `tracing.power: off` for dane; set
`DFTRACER_DISABLE_VARIORUM_POWER=1` and expect **zero** power events in every
trace. Say so explicitly in any corpus README rather than letting a reader
assume power data exists.

The variorum-kills-the-daemon hazard from [[system-matrix]] §6 applies here for
the same reason (two-socket node, service pinned to one core → variorum init
sees `cores(1) mod sockets(2) != 0` and calls `exit()`), so leaving variorum
enabled is not merely useless but actively fatal to `dftracer_service`. See
[[tools-dftracer]].

---

## 6. Filesystems — and the `/p/lustre5` trap

| path | type | use |
|---|---|---|
| `/p/lustre1` (24 P), `/p/lustre2` (24 P) | Lustre `o2ib600` | application data, datasets, per-run output |
| `/p/lustre3` (8 P) | Lustre `o2ib100` | ditto |
| `/p/vast1` (11 P, 80 % full) | VAST/NFS | ditto |
| `/usr/workspace/<user>` (= `/usr/WS2/<user>`) | NFS | source, builds, venvs, **dftracer traces** |

Standard split (`feedback-lustre-io`): **application data on the PFS, dftracer
traces in the session workspace.**

> **There is no `/p/lustre5` on dane.** tuolumne and matrix have it; dane has
> `lustre1..3`. A session `dataset/` symlink created on one of those machines
> **dangles here**, and the failure is silent in the worst way: the traces read
> perfectly while the application writes no output, so a science audit reports
> *"no result file"* for every run and the traces give no hint why. Check before
> running anything:
>
> ```bash
> readlink -f <session>/dataset && test -d <session>/dataset \
>   && echo RESOLVES || echo DANGLES
> ```
>
> Per [[genesis_run]] STEP 5, a sweep's `dataset` symlink is **per-system** for
> exactly this reason — re-point it at `/p/lustre1..3` on dane.

---

## 7. Network and pip — both work here

* `/etc/pip.conf` points at the reachable site Nexus mirror
  (`https://wci-repo.llnl.gov/repository/pypi-group/simple`) and — unlike matrix
  — **there is no user-level `~/.config/pip/pip.conf` shadowing it**. pip works
  out of the box.
* `pypi.org` both resolves **and routes** from the login node, so the matrix
  "resolves but no route" failure does not occur here. The
  `PIP_INDEX_URL`/`PIP_TRUSTED_HOST` pins in `systems.yaml` are belt-and-braces.
* `czgitlab.llnl.gov:7999` (SSH) is **open**, so the [[genesis_run]] STEP 4
  dftracer install from czgitlab works. HTTPS to czgitlab still does not.

---

## 8. Permissions

* **No sudo.** Kernel modules cannot be loaded, which is why §5's power path
  cannot be fixed from inside a job.
* `perf_event_paranoid` = **1** — per-process `perf_event` counters work,
  `perf_event_uncore` does not.
* No GPU, therefore no GPU-profiling permission question at all.

---

## 9. What transfers from which sibling skill

| topic | source | transfers? |
|---|---|---|
| PAPI ceiling / preset partition | [[system-matrix]] §5, [[software-papi]] | ✅ same CPU |
| CPU power being unreadable | [[system-matrix]] §6 | ✅ — and dane has no NVML fallback |
| Slurm workflow, `--overlap`, `--nodelist` | [[slurm-alloc]] | ✅ |
| pip Nexus mirror | [[system-matrix]] §7 | ⚠️ mirror same, but no user-conf override here |
| partition limits / `MaxNodes=1` pdebug | [[system-matrix]] §2 | ❌ completely different (§2) |
| `bash -lc` module rollback | [[system-matrix]] §3 | ❌ does not reproduce here (§3) |
| CUDA/CUPTI | [[system-matrix]] §4, [[software-cupti]] | ❌ no GPU |
| ROCm/HIP, ROCProfiler | [[system-tuolumne]], [[software-rocm]] | ❌ no GPU |
| Flux (`flux alloc`/`flux proxy`) | [[system-tuolumne]], [[flux-alloc]] | ❌ Slurm here |
| `/p/lustre5` paths | tuolumne, matrix | ❌ not mounted (§6) |

---

## 10. Session lessons (dated)

**2026-08-27 — first dane characterisation**

* Registered dane in `systems.yaml` (`srun`, `gcc/12.1.1-magic` +
  `mvapich2/2.3.7`, `tracing.papi_module: ""`, `tracing.power: off`).
* All facts above were measured on the **login node** `dane3`. Compute-node
  verification of PAPI counters and the ppn ladder is **still outstanding**
  because no Slurm bank exists for this user on dane (§0) — every `sbatch`,
  including `--test-only`, is rejected. Nothing here has been confirmed under
  `srun`.
* CPU/PMU facts are expected to hold on compute nodes (identical hardware), but
  `perf_event` availability under a job step is exactly the kind of thing that
  differs between login and compute nodes on TOSS; re-derive it with
  [[software-papi]] once a bank is available, and update §4 with the result.
