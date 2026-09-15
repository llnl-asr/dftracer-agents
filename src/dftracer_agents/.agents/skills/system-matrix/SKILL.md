---
name: system-matrix
description: >
  System profile for matrix (LLNL NVIDIA H100 + Intel Sapphire Rapids, TOSS 4,
  Slurm) — the toolchain that actually works, the three partitions and their
  very different limits and backlogs, where CUPTI really lives, the PAPI
  ceiling, and the two permission walls (MSR/RAPL power, pip index) that block
  things which work elsewhere. Load this skill for any dftracer session, build,
  or job launch on matrix.
---

Cross-references: [[slurm-alloc]] [[software-cupti]] [[software-papi]] [[tools-dftracer]] [[system-tuolumne]] [[workload-minife]]

matrix is the NVIDIA counterpart to [[system-tuolumne]]'s AMD/Cray world.
Almost nothing about the tuolumne profile transfers: different vendor,
different scheduler flavour, different module tree, different power story.

---

## 1. Hardware

Per compute node (measured on a `pdebug` node):

| | |
|---|---|
| GPUs | **4 × NVIDIA H100 80GB HBM3**, compute capability **9.0** (`sm_90`) |
| driver | 610.43.02 |
| CPU | 2 × **Intel Xeon Platinum 8480+** (Sapphire Rapids) |
| cores | 112 effective (56/socket), **8 NUMA regions**, `RestrictedCoresPerGPU=28` |
| memory | ~515 GB |
| OS | RHEL 8.10 / TOSS 4 |
| scheduler | **Slurm** (not Flux) |

Login nodes have **no GPU and no NVIDIA driver** — `nvidia-smi` fails there.
Probe GPU/CUPTI/PAPI facts from a compute node via `srun`, never from the
login node.

---

## 2. Partitions — three of them, wildly different

```
sinfo -o "%20P %5a %10l %6D %10T %N"
scontrol show partition <name> | tr ' ' '\n' | grep -E "MaxNodes|MaxTime|TotalNodes"
```

| partition | MaxNodes | total nodes | MaxTime | typical backlog |
|---|---|---|---|---|
| `pdebug` | **1** | 2 | 1 h | usually empty |
| `pbatch` | 10 | 35 | 24 h | **heavily backlogged** (observed 177 queued, ~4-day start estimate for *any* size) |
| `pci` | 10 | 20 | 24 h | usually near-idle |

> **`pdebug` is `MaxNodes=1`.** Any multi-node job there is rejected outright —
> `srun -p pdebug -N2` answers "Invalid generic resource (gres) specification"
> and `sbatch --test-only -N2` answers "Requested node configuration is not
> available". Neither message mentions MaxNodes, so the cause is easy to
> misread. **A multi-node scaling study cannot use pdebug at all.**

> **Prefer `pci` over `pbatch` for multi-node work.** Same MaxNodes and MaxTime,
> a third fewer nodes, but in practice a fraction of the queue. Always compare
> both before committing — the difference measured once was *4 days vs 1 hour*
> for the same 8-node job:
> ```bash
> for p in pbatch pci; do
>   printf "%s: " $p
>   sbatch -p $p -N8 -t 60 --test-only --wrap=hostname 2>&1 | sed 's/.*to start at //;s/ using.*//'
> done
> ```

---

## 3. Toolchain that works

`module purge` + `module load` inside `bash -lc` **silently rolls back** here —
the login profile reloads the default `intel-classic` + `mvapich2` set and your
loads vanish with no error. Put module loads in a **sourced script file**, then
verify with `which`, never assume (same lesson as
`feedback-flux-proxy-wrapper`).

```bash
module load gcc/12.1.1-magic      # CUDA 12.6 supports gcc <= 13
module load mvapich2/2.3.7        # resolves to the gcc-12.1.1 build
module load cuda/12.6.0
export CUDA_HOME=/usr/tce/packages/cuda/cuda-12.6.0
export CC=$(command -v mpicc) CXX=$(command -v mpicxx)
```

Available: `gcc` 10.3.1–13.3.1, `clang` 14–22, `intel`/`intel-classic`,
MPI = `mvapich2` 2.3.6/2.3.7 or `openmpi/4.1.2` (per-compiler builds under
`/usr/tce/modulefiles/MPI/<compiler>/`).

---

## 4. CUDA and CUPTI — do not trust `/usr/local/cuda`

> **The site-default `/usr/local/cuda-12.0` has NO CUPTI.** No `extras/`, no
> `include/cupti.h`. A build that auto-detects the toolkit will find it, skip
> CUPTI, and disable GPU tracing *silently*.

The module-managed toolkits all ship CUPTI in the merged layout
(`<root>/include/cupti.h`, `<root>/lib64/libcupti.so`):
`cuda/11.8.0`, `12.2.2`, `12.6.0`, `12.9.1`, `13.1.1`.

**Always pin it:** `DFTRACER_CUDA_PATH=/usr/tce/packages/cuda/cuda-12.6.0`.
See [[software-cupti]] for the full backend contract.

`libcupti.so.*` files under `nsight-systems/*/target-linux-x64/` are Nsight's
bundled injection libs — no headers, not usable to build against.

Profiling permission is **open** here: `NVreg_RestrictProfilingToAdminUsers=0`,
so CUPTI attaches as an ordinary user. Verified: `cuptiGetTimestamp` and
`cuptiActivityEnable(CONCURRENT_KERNEL)` both return `CUPTI_SUCCESS`,
`CUPTI_API_VERSION=24`.

---

## 5. PAPI — 17 presets, not 30

The **system** PAPI (`/usr/lib64/libpapi.so.5`, version **5.6.0.0**, an
LLNL-patched build) is the only one that sees the Sapphire Rapids PMU:

* **19 hardware counters**, **17 presets** available, **10 fit at once**
* active component `perf_event` (PMUs `ix86arch, perf, perf_raw, spr`)
* `rapl` component **disabled** ("CPU model not supported")
* `perf_event_uncore` **disabled** (needs `perf_event_paranoid=0`)
* **no CUDA/NVML PAPI component**

> **The `papi/6.0.0.1` module is useless here** — it reports
> **0 hardware counters and 0 presets** on this CPU (it predates Sapphire
> Rapids). Do not "upgrade" to it.

Any plan inherited from an MI300A/Cray system that says "capture all 30 PAPI
presets" must be re-derived: here it is 17, and they do not all fit at once.
The measured exact partition and the counter-dropping trap are in
[[software-papi]].

---

## 6. Power — CPU power is NOT available

| source | status |
|---|---|
| variorum / MSR | ❌ `/dev/cpu/*/msr` is `crw------- root root`; **`msr_safe` module not loaded**; cannot be fixed from inside a job |
| RAPL sysfs | ❌ `/sys/class/powercap/intel-rapl:0/energy_uj` is `-r--------` (root-only); only `name` is world-readable |
| **NVML GPU power** | ✅ **works as an ordinary user** |

```bash
nvidia-smi --query-gpu=index,power.draw,utilization.gpu,memory.used \
           --format=csv,noheader,nounits
```

So on matrix, **sample GPU power with NVML** and treat CPU/package power as
unavailable pending a site-admin change (load `msr_safe`, or relax the RAPL
permissions). Enabling variorum in a dftracer build here is not merely useless
— it is **actively fatal to `dftracer_service`**; see [[tools-dftracer]].

> **`probe_power()` reports three power sources here and NONE of them work.**
> Measured output on a matrix compute node:
> ```
> - gpu: rocm_smi     (/opt/rocm-7.2.0)                  <-- FALSE POSITIVE: no AMD GPU here
> - cpu: rapl_powercap(/sys/class/powercap/intel-rapl)    <-- energy_uj is mode 0400
> - cpu: msr          (/dev/cpu/0/msr)                    <-- root-only, no msr_safe
> ```
> The probe keys on **directory/file existence**, not readability or vendor: matrix has
> `/opt/rocm-6.4.3` and `/opt/rocm-7.2.0` installed despite having only NVIDIA GPUs, so
> the AMD branch fires. This is why `systems.yaml` sets **`tracing.power: off`** for
> matrix — verified to produce `opted_out: True`, `can_enable: False`, so no session
> wastes a build on a power backend that cannot return a single sample here.

---

## 7. pip cannot reach PyPI — use the site Nexus mirror

`pypi.org` resolves (IPv6) but has **no route**; pip retries five times and
dies with `Name or service not known`.

`/etc/pip.conf` correctly points at the reachable site mirror, but a
**user-level `~/.config/pip/pip.conf` written by "NVIDIA PyIndex" overrides it**
and points back at `pypi.org`. Config precedence puts the user file above the
global one, so every pip install in a fresh venv fails.

Fix per-session **via environment** (env beats every config file) rather than
editing the user's global pip.conf:

```bash
export PIP_INDEX_URL=https://wci-repo.llnl.gov/repository/pypi-group/simple
export PIP_TRUSTED_HOST=wci-repo.llnl.gov
export PIP_EXTRA_INDEX_URL=
```

---

## 8. Job-launch notes

* `--overlap` is required to co-schedule a second step (e.g. a telemetry
  daemon) with the application step on the same nodes.
* Pin co-scheduled steps to the same nodes with `--nodelist=<csv>` so that in
  an allocation larger than the job they cannot drift onto other nodes.
* `--gres=gpu:4` exposes all four GPUs to every task; `--gpus-per-task=1`
  restricts each task to one and renumbers it. Which you want depends entirely
  on how the application picks its device — see [[workload-minife]] for a code
  that breaks under `--gpus-per-task`.
* `--kill-on-bad-exit=1` plus an outer `timeout` keeps one dead rank from
  hanging a sweep forever in an MPI collective.

---

## 9. Scheduler: Slurm

matrix runs **Slurm**, not Flux — so none of the `flux alloc` / `flux proxy`
muscle memory from [[system-tuolumne]] transfers. The full workflow, the
mandatory rules, and the traps live in **[[slurm-alloc]]**; load that before any
allocation or job launch here. The matrix-specific facts are:

```bash
salloc  -p pdebug -N1 -t 55 --gres=gpu:4 --no-shell         # N=1 work, starts at once
sbatch  --parsable -p pci -N<n> -t <min> --gres=gpu:4 \
        --job-name=<name> -o <log>/%x_%j.log --wrap "<script>"
srun    --jobid=<J> --overlap -N<n> -n<tasks> --gres=gpu:4 <cmd>
```

**Partition strategy (measured, and it is not obvious):**

* `pdebug` — the only partition that schedules immediately, and `MaxNodes=1`.
  Use it for every single-node run; it cannot serve anything larger.
* `pci` — use this for multi-node work, **not** `pbatch`. Same `MaxNodes=10`
  and 24 h limit, roughly a day of queue instead of three-to-four.
* `pbatch` — largest node pool, worst queue. Last resort.

**Ask exactly for the nodes you need.** Backfill here is driven by node count
and is completely insensitive to walltime (measured across 60/120/240/720 min —
identical estimates). Requesting 8 nodes for a 2-node job cost ~13 hours of
extra queue for no benefit:

| request on `pci` | est. start |
|---|---|
| 2 nodes | ~19 h |
| 4 nodes | ~32 h |
| 8 nodes | ~33 h |

Always confirm with the scheduler rather than guessing:

```bash
sbatch -p <part> -N<n> -t <min> --gres=gpu:4 --test-only --wrap="hostname"
```

> Remember `pci ⊂ pbatch` (§2): a pending `pci` job reporting *"reserved for
> jobs in higher priority partitions"* is being outranked on shared nodes, not
> hitting broken hardware.

---

## 10. Filesystems

| path | use |
|---|---|
| `/p/lustre1`, `/p/lustre2`, `/p/lustre5` | **Lustre PFS** — all application data, datasets, checkpoints, per-run output |
| `/usr/workspace/<user>` (= `/usr/WS2/<user>`) | NFS workspace — source, builds, venvs, **dftracer traces** |

Same split as every other site (see `feedback-lustre-io`): **application data on
Lustre, dftracer traces in the session workspace.** A session's `dataset/` should
be a symlink to a Lustre directory, and any stage that runs the application
should point the app's own working directory at `<WS>/dataset/<run>/` so output
physically lands on the PFS.

Lustre here uses a **composite (PFL) layout** by default — `lfs getstripe -d`
reports `lcm_entry_count > 1` rather than a single stripe count. Read the
per-component layout before making any striping claim; a flat
"stripe_count = N" statement is not meaningful against a PFL default.

---

## 11. Permissions

* **No sudo, ever.** Kernel modules cannot be loaded, which is exactly why the
  variorum/MSR power path in §6 is unavailable and cannot be worked around from
  inside a job.
* CUDA/CUPTI profiling **is** permitted for ordinary users
  (`NVreg_RestrictProfilingToAdminUsers=0`) — unusually permissive, and worth
  confirming rather than assuming on a sibling cluster.
* `perf_event_paranoid` is restrictive enough to disable PAPI's
  `perf_event_uncore` component; per-process `perf_event` counters still work.

---

## 12. Session lessons (dated)

**2026-08-26 — a full genesis trace sweep (miniFE, CUDA variant, 1176 runs)**

* The build/annotate/run traps for the app itself are in [[workload-minife]];
  the dftracer service traps are in [[tools-dftracer]]; CUPTI is in
  [[software-cupti]]; the exact PAPI partition is in [[software-papi]].
* Single-node throughput measured on `pdebug`: ~**5.5 runs/min** for a
  1-node/1-4-rank miniFE run with tracing, service telemetry and GPU-power
  sampling all on — i.e. a 294-run single-node sweep completes in about an hour.
  Tracing overhead was not the bottleneck; job-launch latency was.
* A whole node-scale row is best driven as **one non-blocking `sbatch` per
  scale**, submitted together, rather than a `--wait` chain — otherwise a single
  deep-queued large job blocks all the smaller work behind it.

## Partitions share nodes — "reserved for higher priority" is contention, not breakage

Check whether two partitions are drawn from the same hardware:

```bash
sinfo -h -p A -o %N ; sinfo -h -p B -o %N
```

If A's nodes are a subset of B's and B outranks A, jobs pending in A report
*"reserved for jobs in higher priority partitions"*. That is ordinary contention on shared
hardware, **not** a broken or drained node — do not go hunting for a fault.

## Slurm: three traps that make multi-node jobs fail in ways that name the wrong cause

Measured during a genesis sweep on this machine. All three cost hours before being
root-caused, and none of the error messages point at the real problem.

### Cores are bound to GPUs, so a default allocation gets 28 of 112 cores

`RestrictedCoresPerGPU=28` with 4 GPUs per node. A job that does **not** request
GPUs is given only **28 cores per node**, not 112. Worse, in a **multi-node**
allocation every `srun` step that asks for a SUBSET of the job's nodes then dies
with:

```
srun: error: Unable to create step for job <id>: Invalid generic resource (gres) specification
```

which names gres, not cores, and not the node count. Measured: at `N=1` every step
form succeeds; at `N=4` every step form fails, and the only cells that passed were
the ones whose slice happened to be the WHOLE allocation.

**Fix:** allocate with `--exclusive --gres=gpu:4` even for a CPU-only app. Requesting
all 4 GPUs releases all 112 cores, and `--exclusive` keeps the node un-shared (two
tenants otherwise contaminate each other's PAPI counters and per-host power).

### `DefMemPerCPU=4599` silently caps a `-c 1` rank at ~4.5 GB

`SelectTypeParameters=CR_CORE_MEMORY`, so memory is allocated per CPU. A rank asking
for one core gets ~4.49 GB **no matter how idle the 503 GB node is**. A rank needing
30.8 GB is OOM-killed about 4 s in, during setup.

**Fix:** add `--mem=0` to the application `srun` step (all node memory). Nodes are
already held `--exclusive`, so this affects no other tenant, and CPU-per-rank stays
the experimental control.

**The trap inside the trap:** a memory-probe run with `-c 8` gets ~36 GB and passes
cleanly, "validating" a configuration the real sweep never runs. Probe with the same
`-c` the workload will actually use, or the probe certifies the wrong thing.

### `--exclusive` is often unschedulable here, and that is not a queue-depth problem

Other tenants hold 28-core slices, leaving most nodes in state `mixed`. An
`--exclusive` request needs a WHOLLY free node, so it waits behind backfill
indefinitely: measured 38 h pending with the estimated start time *receding*
(12:20 -> 15:10 -> 04:22 -> 11:43 -> next day) rather than converging. Shortening the
walltime does not help; dropping `--exclusive` does not help either once GPUs are
requested. Re-probe with `sbatch --test-only`, and expect estimates to be snapshots
rather than commitments.

## A background driver can survive on a DIFFERENT login node

A long-running submitter started from an interactive session lives on whichever login
node that session was on. If the session later lands on another login node, every
local `ps`/`/proc` search finds nothing while the driver happily goes on submitting —
so "I killed it" is reported repeatedly and is repeatedly wrong. One survived every
cleanup for half a day this way.

Search all login nodes, and confirm death by an *effect* (its log stopped advancing),
never by a failed process search:

```bash
for h in <login-nodes>; do
  ssh "$h" 'for p in /proc/[0-9]*; do
      a1=$(tr "\0" "\n" < $p/cmdline 2>/dev/null | sed -n "2p")
      case "$a1" in */driver.sh) echo "$h $(basename $p)";; esac
    done'
done
```

Match on **argv[1] being exactly the script path**. A substring match on the whole
command line also matches the monitoring shell that mentions the script, which is how
`pkill -f <script>` kills the session doing the killing (observed twice, exit 144).

## `WaitTime=30` silently truncates the trace of any co-scheduled daemon

This site sets **`WaitTime = 30`** (`scontrol show config`). srun terminates *all
remaining tasks* 30 s after the **first** task in a step exits.

A node-counter daemon launched as one task per node (`-N n -n n -c1`) finishes at a
slightly different time on each node, because each node's trace is a different size
and is gzipped at shutdown. So the fastest node's daemon exits, srun starts its
30 s timer, and the slower node's daemon is **SIGKILLed mid-flush**.

The symptoms name nothing useful:

* exactly ONE `service_<host>.pfw.gz` is a truncated gzip, never an app trace
* `srun: error: <host>: task N: Killed`, and the step's `wait` returns **137**
  about **31 s** after teardown began
* `sacct` records the step as **`CANCELLED by <uid>`** — your own uid, because srun
  cancels on your behalf, which reads as "my harness killed it" and sends you
  looking in the wrong place entirely
* the application step is `COMPLETED` with `rc=0` and every app trace intact

**Fix: `--wait=0` on any srun step whose tasks legitimately exit at different
times** (0 = never terminate stragglers). Nothing else works: the daemon is not
slow, so a longer flush wait does not help, and making its `stop` synchronous does
not help either — the step is being killed from outside.
