---
name: software-mpi
description: >
  MPI and MPI-IO (ROMIO) optimization strategies, Cray MPICH environment
  variable wiring, flux proxy env propagation rules, and dftracer MPI tracing
  requirements.  Load this skill for any MPI or collective I/O work.
---

Cross-references: [[dftracer-io-optimization]] [[dftracer-preload-run]] [[workload-ior]] [[software-hdf5]]

---

## L2 Software — MPI-IO / ROMIO

Applies when bottleneck is `small_io_pct` or `rand_pct` high (metric: bandwidth or time).

### Hints file approach

```bash
cat > <WS>/romio_hints.txt << 'EOF'
cb_buffer_size 67108864
romio_cb_read enable
romio_ds_read enable
romio_ds_write enable
cb_nodes <num_nodes>
EOF
export ROMIO_HINTS=<WS>/romio_hints.txt
```

### Cray MPICH environment variable (preferred over hints file)

**Format:** `pathname_pattern:key=value:key=value` — colon-separated key=value pairs per pattern.
Multiple file patterns are comma-separated. Use `*` to match all files.

```bash
# Correct format — colons between key=value pairs:
export MPICH_MPIIO_HINTS="*:romio_cb_write=enable:cb_buffer_size=67108864:cb_nodes=16:romio_ds_write=disable:striping_factor=16:striping_unit=4194304"

# To display hints actually applied (set before run):
export MPICH_MPIIO_HINTS_DISPLAY=1
```

**NEVER pass via `--env` to `flux proxy flux run`** — colons and semicolons in the value
cause flux run to lose the binary argument entirely. Always use a wrapper script:

```bash
cat > wrapper.sh << 'EOF'
#!/bin/bash
export MPICH_MPIIO_HINTS="*:romio_cb_write=enable:cb_buffer_size=67108864:cb_nodes=16"
exec "$@"
EOF
chmod +x wrapper.sh
flux proxy $JOB flux run -N 2 -n 192 --env LD_LIBRARY_PATH=<libs> bash wrapper.sh /path/to/binary args
```

**How to discover available hints on a new Cray MPICH version:**

```bash
strings /opt/cray/pe/mpich/<version>/ofi/cray/<ver>/lib/libmpi_cray.so \
  | grep -E "^romio_|^cb_|^striping_|^ind_|^ds_" | sort -u
```

### Cray MPICH 9.0.1 — confirmed available hints

Extracted from `libmpi_cray.so` on Tuolumne (cray-mpich/9.0.1, OFI path):

| Hint | Recommended value | Effect |
|------|------------------|--------|
| `romio_cb_write` | `enable` | Collective buffering for writes |
| `romio_cb_read` | `enable` | Collective buffering for reads (disable on VAST/NVMe) |
| `romio_ds_write` | `disable` | Data sieving writes (disable to force 2-phase I/O) |
| `romio_ds_read` | `disable` | Data sieving reads |
| `cb_buffer_size` | `67108864` | 64 MB collective buffer per aggregator |
| `cb_nodes` | `16` | Aggregator count (match Lustre stripe count) |
| `cb_config_list` | `*:1` | Explicit aggregator mapping |
| `striping_factor` | `16` | Lustre stripe count (match with `lfs getstripe`) |
| `striping_unit` | `4194304` | 4 MB stripe size (match Lustre layout) |
| `ind_rd_buffer_size` | `4194304` | Independent read buffer |
| `ind_wr_buffer_size` | `4194304` | Independent write buffer |
| `romio_lustre_cb_lock_ahead_write` | `enable` | Lock-ahead for collective writes on Lustre |
| `romio_lustre_cb_lock_ahead_read` | `enable` | Lock-ahead for collective reads on Lustre |
| `romio_lustre_cb_lock_ahead_num_extents` | `4` | Number of lock-ahead extents |
| `romio_no_indep_rw` | `true` | ❌ NOT SUPPORTED on cray-mpich 9.0.1 — hint silently dropped with "unrecognized value" warning |

### cb_nodes guidance for Lustre + cray-mpich 9.0.1

Per-workload rules validated on Tuolumne lustre5 (28 OSTs), 2 nodes × 192 ranks:

| Workload type | cb_nodes rule | Rationale |
|---|---|---|
| Contiguous fixed-size writes | Do NOT set (use default=2) | Default aggregators work well; adding more aggregators doesn't improve throughput and can cause pwrite stalls |
| Variable-size writes (e.g. normal-dist) | Do NOT set (use default=2) | More aggregators fragment variable chunks → massive pwrite p95 regression (+2650%) |
| Reads (untested on lustre5) | 2× stripe_count recommended per literature | Follows user's 2× rule |

The user's rule (cb_nodes = 2× stripe_count) is theoretically correct but empirically neutral-or-worse on Tuolumne at the tested scale (DIM_1=16M, 4 TS, 192 ranks). The close overhead savings from more aggregators are cancelled by pwrite stalls caused by buffer fragmentation.

If cb_nodes is needed: test at each scale step (R11) before production. Values tested:
- cb_nodes=16 + cb_buffer_size + striping hints → segfault (see workload-h5bench SKILL.md)
- cb_nodes=32 alone (write, 16-OST) → neutral (open −49%, overall ±0%)
- cb_nodes=56 alone (ND, 28-OST) → neutral (close −96%, pwrite p95 +2650%)

### Common ROMIO parameters (all MPI implementations)

| Parameter | Recommended value | Effect |
|-----------|------------------|--------|
| `cb_buffer_size` | `67108864` (64 MB) | Collective I/O buffer per aggregator |
| `romio_cb_read` | `enable` | Aggregate reads via cb (helps Lustre; hurts VAST — see below) |
| `romio_cb_write` | `enable` | Aggregate writes via cb (beneficial on most systems) |
| `romio_ds_read` | `enable` | Data sieving for reads |
| `romio_ds_write` | `enable` | Data sieving for writes (default; disabling is FATAL on VAST) |
| `cb_nodes` | `<num_nodes>` | Number of aggregator processes |

### Storage-specific ROMIO guidance (check FS_TYPE before applying — see Step 8-PRE of [[dftracer-pipeline]])

See [[workload-ior]] for quantified results on VAST NVMe (Tuolumne):
- `romio_cb_write=enable` → **+58% write BW** (always beneficial)
- `romio_cb_read=enable` → **−70% read BW** on VAST (AVOID on NVMe)
- `romio_ds_write=disable` → **FATAL** on VAST (352→95 MiB/s write collapse)

General rule: collective read buffering helps on high-latency storage (Lustre,
spinning disk). It hurts on parallel NVMe where concurrent reads are cheap.

---

## Forwarding Environment Variables to MPI Ranks

`mpirun` does **not** automatically forward shell environment. Export + `-x`:

```bash
export DFTRACER_ENABLE=1
export DFTRACER_INIT=PRELOAD
export DFTRACER_DATA_DIR=/tmp
export DFTRACER_LOG_FILE=/tmp/traces/myapp
export MPICH_MPIIO_HINTS="*:romio_cb_write=enable"
export LD_PRELOAD=<dftracer_lib>/libdftracer_preload.so

mpirun --allow-run-as-root -np 4 \
  -x DFTRACER_ENABLE \
  -x DFTRACER_INIT \
  -x DFTRACER_DATA_DIR \
  -x DFTRACER_LOG_FILE \
  -x MPICH_MPIIO_HINTS \
  -x LD_LIBRARY_PATH \
  -x LD_PRELOAD \
  ./my_app
```

---

## Flux Proxy — Environment Variable Propagation

`flux proxy` does NOT export the current shell's environment to the broker.
Variables set after `flux alloc` or `flux proxy` are invisible to job tasks.

**Always use `--env` flags with `flux run`:**

```bash
flux proxy <JOBID> flux run \
  -N 2 -n 192 \
  --env MPICH_MPIIO_HINTS="*:romio_cb_write=enable" \
  --env DFTRACER_ENABLE=1 \
  --env DFTRACER_LOG_FILE=<prefix> \
  --env DFTRACER_DATA_DIR=all \
  --env DFTRACER_INC_METADATA=1 \
  --env DFTRACER_INIT=FUNCTION \
  --env LD_LIBRARY_PATH=<libs> \
  <command>
```

Do NOT rely on `export VAR=value` before `flux proxy` — it will NOT propagate.

---

## MPI tracing in dftracer

dftracer must be built with MPI support to capture `MPI_File_write/read` events:

```bash
cmake -DDFTRACER_ENABLE_MPI=ON ...
# Verify:
grep DFTRACER_MPI_ENABLE <prefix>/include/dftracer/core/dftracer_config.hpp
# Expected: #define DFTRACER_MPI_ENABLE 1
```

Runtime: the MPI shared library at run time must match the version dftracer was
compiled against:
```bash
strings libdftracer_core.so | grep "openmpi/include"  # must match system mpi.h path
```

### Cray PE / MPICH include path issue in clang_syntax_check

```
fatal error: mpi.h: No such file or directory
```

Cray PE `mpicc --showme:incdirs` outputs `-I/path` (with `-I` prefix) instead of
a plain path, so auto-detection fails. Always pass explicitly:

```python
clang_syntax_check(run_id=..., filepath=...,
  extra_include_dirs=[
    "/opt/cray/pe/mpich/<version>/ofi/cray/<ver>/include",
    "<ws>/venv/lib/python3.*/site-packages/dftracer/include"
  ])
```

Get the exact MPI path: `mpicc -show | grep -o '\-I[^ ]*' | head -1`

---

## NUMA Memory Binding (MPI processes)

```bash
numactl --hardware   # check topology
numactl --cpunodebind=0 --membind=0 <run_command>   # pin to NUMA node (no persistent side effect)
# For MPI:
mpirun --map-by numa:pe=<cores_per_node>   # OpenMPI
```

---

## Citation

**WisIO (Yildirim et al., ICS 2025)** — covers `small_io_pct`, `rand_pct`, `read_time`, `write_time`
URL: https://dl.acm.org/doi/10.1145/3721145.3730395

**Drishti (Bez et al., PDSW 2022)** — L1/L2/L3 suggestion model including collective I/O
URL: https://ieeexplore.ieee.org/document/10027503

## Failed Configurations

Entries below were applied during optimization loops and caused regressions or had no effect.
Check this section before proposing any configuration for this workload/software/filesystem.

Format per entry:
  date, app, workload, filesystem, system, bottleneck,
  config_attempted, result, metrics_before, metrics_after, delta,
  root_cause, do_not_use_when

<!-- New failed-config entries are appended below by the optimization loop (Step 8d-iii-FAIL) -->

---

## Cray MPICH: `cb_nodes` is accepted but IGNORED (2026-07-08, measured)

Setting `cb_nodes` via `MPICH_MPIIO_HINTS` (or a `ROMIO_HINTS` file) does **not**
by itself increase the number of MPI-IO collective-buffering aggregators on Cray
MPICH. `MPICH_MPIIO_HINTS_DISPLAY=1` will happily echo back `cb_nodes = 8`, while
the runtime keeps using only **2** aggregators.

**Do not trust the hints display — verify the real aggregator count from traces:**
count the distinct ranks that issue writes `>= 1 MB` (the collective-buffer
flushes). Tiny writes happen on every rank (logs), so counting "ranks that write"
is useless; count ranks doing *large* writes.

```bash
dftracer_view -d <split_dir> --query 'cat == "POSIX"' --stream --no-metadata \
  | python3 -c 'import sys,json;p=set()
for l in sys.stdin:
  if "\"name\":\"pwrite\"" not in l and "\"name\":\"write\"" not in l: continue
  e=json.loads(l); r=e.get("args",{}).get("ret",0)
  if isinstance(r,int) and r>=1048576: p.add(e["pid"])
print("aggregators:",len(p))'
```

**What actually raises the aggregator count:** the Cray-specific
`CRAY_CB_NODES_MULTIPLIER` env var (used together with `cb_nodes`), plus a Lustre
stripe count wide enough to absorb them.

Measured on Flash-X (384 ranks / 8 nodes, 18 checkpoints, ~6.7 GB, Lustre):

| Config | aggregators | critical-path write | avg large write |
| --- | --- | --- | --- |
| parallel HDF5, no hints | 2 | 6.80 s | 1.05 MB |
| `+ cb_nodes=8` (hint echoed, ignored) | 2 | 5.53 s | 1.05 MB |
| `+ cb_nodes=16` + `CRAY_CB_NODES_MULTIPLIER=2` + 16×4 MB stripes | **16** | **1.45 s** | **3.76 MB** |

Top-1 rank's share of write bytes fell 52% → 6.8% (balanced parallel I/O).

**Ordering matters:** collective-buffering hints are worthless until the
application actually performs parallel writes. If one rank holds ~all write
bytes, fix that first (see [[workload-flashx]] serial-HDF5 case) — hints cannot
parallelize a single writer.


## mpi4py on Cray MPICH (Tuolumne, 2026-07-09)

**Symptom:** mpi4py generic PyPI wheel (`mpi4py>=4.0`) on Tuolumne with cray-mpich fails to import with:
```
RuntimeError: cannot load MPI library
  /..../libmpi.so.12: cannot open shared object file
```

**Root cause:** mpi4py manylinux wheel expects standard MPICH SONAME `libmpi.so.12`, but Cray PE provides `libmpi_cray.so.12`. Generic wheel built for GNU MPI wrappers; Cray's soname differs.

**Fix (working recipe for Python 3.13 + cray-mpich/9.1.0):**

1. Download and extract wheel manually (pip rename fails on NFS):
   ```bash
   pip download mpi4py --python-version 313 --no-deps -d $WS/tmp
   cd $WS/tmp && unzip -q mpi4py-4.1.2-cp313-cp313-manylinux1_x86_64.manylinux_2_5_x86_64.whl
   ```

2. Patch extension RPATH to find Cray MPI lib + venv lib for symlink:
   ```bash
   CRAY_MPI_LIB="/opt/cray/pe/mpich/9.1.0/ofi/crayclang/20.0/lib"
   VENV_LIB="$WS/venv/lib"
   patchelf --set-rpath "$VENV_LIB:$CRAY_MPI_LIB" \
     mpi4py/MPI.mpich.cpython-313-x86_64-linux-gnu.so
   ```

3. Create symlink to resolve SONAME mismatch:
   ```bash
   ln -sf "$CRAY_MPI_LIB/libmpi_cray.so.12" "$VENV_LIB/libmpi.so.12"
   ```

4. Re-zip and install with ABI env var:
   ```bash
   zip -q -r mpi4py-patched.whl mpi4py/ mpi4py-*.dist-info/
   export MPI4PY_MPIABI=mpich
   pip install --force-reinstall --no-deps mpi4py-patched.whl
   ```

5. Verify import succeeds:
   ```bash
   python -c "from mpi4py import MPI; print(MPI.Get_version())"
   ```

**Environment persistence:**
Record `MPI4PY_MPIABI=mpich` and updated `LD_LIBRARY_PATH` in the session env script so all downstream steps inherit them:
```bash
export MPI4PY_MPIABI=mpich
export LD_LIBRARY_PATH="$WS/venv/lib:/opt/cray/pe/mpich/9.1.0/ofi/crayclang/20.0/lib:${LD_LIBRARY_PATH}"
```

**Key caveat:** `--no-binary=mpi4py` does NOT work on Python 3.13 + NFS (build succeeds but pip atomic-rename to NFS fails; manual extraction avoids pip's rename).


## MPI_Barrier that "dominates" is usually a load-imbalance SINK, not a cost

**Symptom.** `MPI_Barrier` is ~99% of all MPI time and looks like the top bottleneck.

**Root cause.** In a DL trainer whose checkpoint is already `world_rank == 0`-gated, the
barrier does not *cost* time — it *absorbs* whatever wait the slowest rank imposes
(dataloader stragglers, the serialized rank-0 save). It is where imbalance is billed.

**Diagnostic tell.** As you fix the real upstream problem, per-barrier MEAN duration goes
*up* while wall clock goes *down*. Measured on ScaFFold (32 ranks): mean barrier
262 ms -> 657 ms across variants while `total_train_time` fell 1.97 s -> 1.64 s.

**Fix.** Attack the upstream imbalance (e.g. `dataloader_num_workers > 0` for prefetch and
compute/IO overlap). Do not do "barrier surgery," and do not rank by barrier aggregate.
Rank by wall clock / FOM.

**Anti-pattern: "do-less" levers.** Raising `checkpoint_interval` writes fewer checkpoints,
so any speedup is partly from doing less work, not from going faster. Verify total bytes /
data volume is unchanged before crediting a speedup. On ScaFFold it was both inferior to the
overlap fix AND did not compound with it.

**Tool caveat.** `comparator` reported MPI_Barrier p50 pinned at 167.79 ms across every
variant — a fixed time-bucket artifact. Use mean/p95/p99 and wall clock, not barrier p50.

### CORRECTION (measured at a realistic run length)

The section above was inferred from a run whose training phase was ~2 seconds. At a fixed
1200-epoch (~12 min) run on the same 32-rank ScaFFold workload, `MPI_Barrier` aggregate is
**16.8 s across 32 ranks against 749.9 s of training (~2%)** — it is not the top bottleneck at
all. The "barrier = 99% of MPI time / ~65% of wall" reading was an artifact: in a 2-second run,
startup, teardown, and the first-epoch stragglers dominate everything.

What survives, and is the durable lesson:

- **Never rank bottlenecks from a run that is too short.** Fix a time budget (>= ~10 min of
  training), calibrate the epoch count on the baseline, and hold it constant across variants.
- A barrier still *absorbs* upstream imbalance, so attack the upstream cause. On this workload
  the real cost was POSIX I/O time (5666 s -> 801 s, -86%), removed by dataloader prefetch
  (`dataloader_num_workers > 0`), not by barrier surgery.
- Barrier mean duration rising while wall time falls remains a valid tell that the barrier is
  a sink rather than a cost — but confirm the aggregate is actually a meaningful fraction of
  wall clock before acting on it.

## Barrier-dominated benchmark harnesses: check the call site before proposing anything (2026-08-05)

Two independent workloads on the same Cray MPICH system have now shown `MPI_Barrier` time
that belongs to the BENCHMARK'S OWN TIMING HARNESS, not to the application's algorithm
(IOR's timing loop; RAJAPerf's `KernelBase::startTimer/stopTimer`). Before proposing any
communication fix for a barrier finding, `grep -rn MPI_Barrier` the source and classify each
call site as harness vs. algorithmic. Harness barriers are off-limits (reducing them changes
what is measured) and the only remaining lever is collective-algorithm selection.

`MPICH_SHARED_MEM_COLL_OPT=1` (node-local shared-memory collective algorithms) is the
standard env-var-only lever for that case — but it has already measured **no_change (-1.6%,
inside noise)** on this system for IOR's barrier/allreduce harness at 64 ranks/node
(`opt_kb`, `software/L2/tuolumne/ior/cray-mpich`). Propose it, but state up front that the
prior on this system is null; it is worth a slot only when the barrier bucket is large
(>= several % of wall time) AND ranks are densely packed per node so a node-local algorithm
has something to exploit. Citation for the mechanism: Thakur et al., IJHPCA 19(1) 2005,
https://doi.org/10.1177/1094342005051521; runtime algorithm selection modelling:
Nuriyev & Lastovetsky, https://arxiv.org/pdf/2004.11062v1.
