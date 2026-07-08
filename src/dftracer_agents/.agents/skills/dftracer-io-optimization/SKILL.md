---
name: dftracer-io-optimization
description: Key literature, bottleneck-to-optimization mappings, and strategies for the dftracer I/O optimization pipeline
---

## MANDATORY: Tool-First Analysis and Explicit Separation

### Tool-First Rule
Every analysis and diagnosis step MUST attempt MCP tools before falling back to
manual methods. The canonical tool order is:

1. `mcp__dftracer__analyze` — dfanalyzer trace analysis
2. `mcp__dftracer__diagnose` — dfdiagnoser bottleneck scoring
3. `mcp__dftracer__comparator` — compare two runs
4. `mcp__dftracer__event_count` — event count summary
5. `mcp__dftracer__reader` — trace metadata reader
6. `mcp__dftracer__session_analyze_traces` — session-scoped analysis

If tools are unavailable, ask the user to start the dftracer MCP server.
If tools error, fix the tool or wiring before using custom Bash/Python.

### Explicit Separation Requirement
In every report, create a table separating:
- **TOOL FINDINGS:** Results from MCP tools (dfanalyzer, dfdiagnoser, comparator)
- **MANUAL ANALYSIS:** Results from custom Bash/Python (only when tools fail)

Never conflate the two. Label each finding with its source.

### Citation-Backed Optimizations (MANDATORY)
Every optimization proposal MUST carry a verifiable paper citation. Use the
Built-in Citations section below (WisIO, Drishti, GLANCED-IO, etc.), or search
arXiv / Semantic Scholar. The citation must include: authors, title, venue/year,
and a URL (arXiv PDF, ACM DOI, or IEEE Xplore). If no paper is found after
10 search attempts, mark the proposal as UNSUPPORTED and do not apply it.

### NEVER "Do Less" as Optimization
The following are FORBIDDEN:
- "Reduce checkpoint frequency" or "write fewer checkpoints"
- "Reduce plot variables" or "write less data"
- "Do less I/O", "do less compute", "do less communication", "use less memory"
- Any proposal whose core mechanism is reducing the amount of work done

**Why:** Doing less is not a solution. The goal is to make the SAME work run
faster (better bandwidth, lower latency, higher throughput), not to avoid the
work. If the bottleneck is write-time, propose buffering, async I/O, collective
I/O, compression with faster algorithms, or stripe tuning — never "write less."

---

## MANDATORY: Datasets Must Live on Lustre, Never NFS

Application datasets (training data, fractals/checkpoints/runs, any file the
app reads/writes repeatedly during a run) **must be placed on Lustre**
(`/p/lustre5/$USER/...`), never on NFS-backed paths (e.g. `/usr/WS*`,
`/g/g*`, `/collab/...`). NFS is a single-server filesystem with no striping
— it hard-caps aggregate bandwidth/IOPS regardless of how well the
application layer (num_workers, prefetch_factor, persistent_workers) is
tuned. This was confirmed directly on Tuolumne: a ScaFFold fractal dataset
sitting on NFS (`cz-ws2-nfs-new.llnl.gov`) showed critical `posix_*_ops_slope`
bottlenecks and only ~41% compute/I-O overlap even with L1-optimal
DataLoader settings; moving the same dataset to Lustre with matched striping
removed the ceiling entirely without any app-level code change.

**Before running any benchmark/training job**, check where the dataset
directory actually lives:
```bash
stat -f <dataset_dir>          # look for "Type: nfs" vs "Type: lustre"
df -T <dataset_dir>             # filesystem type column
```
If it's NFS, copy it to Lustre and repoint the app's data-dir argument —
do not proceed with performance analysis on an NFS-resident dataset, since
any I/O bottleneck found there may just be "wrong filesystem," not a real
app/library issue.

### Set Lustre striping to match the access pattern

Striping must be sized to the **actual per-file size and per-I/O transfer
size observed in the trace**, not applied blindly:

| Access pattern (from trace)                          | Stripe count            | Stripe size                          |
|--------------------------------------------------------|--------------------------|----------------------------------------|
| Many small files, each < stripe size (e.g. ML per-sample files, KB–few MB) | `1` — striping a tiny file across multiple OSTs adds overhead with no parallelism benefit; parallelism instead comes from many *files* being spread across the directory/filesystem | leave at filesystem default |
| Few large shared files (checkpoints, HDF5, single big dataset file) read/written by many ranks concurrently | OST count, capped 8 for < 1 GB files, 16+ for very large files | 1–4 MB, aligned to the app's read/write transfer size (see `dftracer_info`/diagnose `*_avg_transfer_size`) |
| Sequential large writes (checkpoints) | 4–8 | 4 MB (matches ROMIO `cb_buffer_size` default of 64 MB / 16 aggregators) |

Apply with `lfs setstripe -c <count> -S <size> <dir>` **before** any file is
created in that directory (striping is set at file-creation time and cannot
be changed retroactively without rewriting the file). For a directory of
many small per-sample files (the common ML dataset case), the correct call
is simply `lfs setstripe -c 1 <dataset_dir>` — do NOT default to a high
stripe count "for safety"; it actively hurts small-file access.

Verify after copying data in: `lfs getstripe -c <dataset_dir>` should report
the value you set, and `lfs getstripe <sample_file>` should show a single
OST for small-file datasets.

## Related Skills

Software-specific strategies are also available in dedicated skills:
- **[[software-mpi]]** — MPI-IO/ROMIO details, Flux env propagation, Cray MPICH
- **[[software-hdf5]]** — HDF5 version compatibility, chunk/cache tuning, build from source
- **[[software-posix]]** — POSIX readahead, Lustre striping, OS/VM tuning, ops_slope bottlenecks

Workload-specific results:
- **[[workload-ior]]** — quantified ROMIO results on VAST NVMe (Tuolumne)
- **[[workload-h5bench]]** — HDF5 CMake build and annotation pitfalls


System-specific accelerators (L3 near-node storage tiers):
- **[[system-tuolumne-rabbit]]** — Rabbit near-node flash on Tuolumne (XFS/GFS2/Lustre via Flux `-S "#DW ..."`); stage hot data onto a local flash tier to relieve network-Lustre bottlenecks

---

## Key Reference Papers

### WisIO (primary bottleneck → optimization guide)

**Citation:** Yildirim, Izzet, Hariharan Devarajan, Anthony Kougkas, Xian-He Sun, and
Kathryn Mohror. "WisIO: Automated I/O Bottleneck Detection with Multi-Perspective Views
for HPC Workflows." In *Proceedings of the 39th ACM International Conference on
Supercomputing*, pp. 749–763. 2025.

**What it provides:**
- Multi-perspective bottleneck taxonomy covering: sequentiality, small-I/O,
  read/write time fraction, metadata overhead, fetch pressure, stragglers
- Per-category root cause + optimization strategy mapping
- Quantified thresholds for high/critical classification

**Bottleneck → optimization mapping from WisIO:**

| WisIO category      | Metric in dfdiagnoser | Primary fix (L1→L3)                              |
|---------------------|-----------------------|--------------------------------------------------|
| small-io            | small_io_pct          | L1: buffer reads; L2: collective I/O; L3: stripe |
| sequentiality       | rand_pct / seq_pct    | L1: sort access; L2: prefetch hint; L3: readahead|
| read-time           | read_time_pct         | L1: async I/O; L2: cb_buffer; L3: blockdev setra |
| write-time          | write_time_pct        | L1: async write; L2: dirty tuning; L3: vm.dirty  |
| metadata            | metadata_time_pct     | L1: cache stats; L2: HDF5 metadata opt; L3: MDT  |
| fetch-pressure      | fetch_pressure        | L1: DataLoader workers; L2: prefetch_factor       |
| stragglers          | epoch_straggler       | L1: sort by size; L2: persistent_workers          |


### Drishti (user-facing optimization guidance)

**Citation:** Bez, Jean Luca, Hammad Ather, and Suren Byna. "Drishti: Guiding
end-users in the I/O optimization journey." In *2022 IEEE/ACM International Parallel
Data Systems Workshop (PDSW)*, pp. 1–6. IEEE, 2022.

**What it provides:**
- End-user-facing optimization suggestions organized by I/O category
- Three-level suggestion structure (application / library / system) matching our L1/L2/L3
- Estimated improvement ranges per suggestion type

**Drishti suggestion model:**

| Drishti category | L1 (app)                     | L2 (software)               | L3 (system)                |
|------------------|------------------------------|-----------------------------|----------------------------|
| small-io         | buffer reads, batch writes   | ROMIO cb_buffer, HDF5 chunks| lfs setstripe -S 4m        |
| metadata         | cache stat(), open once      | H5Pset_coll_metadata_write  | lfs mkdir -c N (DNE)       |
| sequentiality    | sort indices, posix_fadvise  | romio_ds_read=enable        | blockdev --setra 4096      |
| shared-file      | independent file per rank    | romio_cb_read=enable        | increase OST count         |


## Optimization Metric Selection Guide

Use this when the agent must suggest which metric to optimize:

| Dominant bottleneck(s)              | Recommended metric | Rationale                        |
|-------------------------------------|--------------------|----------------------------------|
| read_time_pct or write_time_pct ≥4  | time               | Latency is the binding constraint|
| read_bw_mean or write_bw_mean low   | bandwidth          | Throughput is the binding limit  |
| small_io_pct or rand_pct ≥4         | iops               | Operation rate limits throughput |
| metadata_time_pct ≥4                | metadata_ops       | Metadata is the bottleneck       |
| multiple ≥4                         | time (default)     | Most general; addresses all      |


## Common Parameter Recommendations (from WisIO + Drishti)

- **Buffer size:** 4 MB aligned to Lustre stripe size (1 MB default stripe)
- **ROMIO collective buffer:** 64 MB (`cb_buffer_size=67108864`)
- **Lustre stripe count:** 4–8 for files < 10 GB; 16+ for very large files
- **Linux readahead:** 2–8 MB (`blockdev --setra 4096` to `16384`)
- **DataLoader workers:** `cpu_count // 2` with `prefetch_factor=4`
- **PyTorch checkpoint:** async save in background thread; use lz4 compression
- **vm.dirty_ratio:** 10–20% for write-heavy; 5–10% for checkpoint workloads


## Artifact Files Produced by optimize.yaml

| File                        | Contents                                     |
|-----------------------------|----------------------------------------------|
| `optimization_context.json` | Metric, bottlenecks, papers — shared context |
| `opt_l1_results.json`       | L1 proposals, status, files modified         |
| `opt_l2_results.json`       | L2 proposals, env vars applied               |
| `opt_l2_env.sh`             | Source before each run to keep L2 settings   |
| `opt_l3_results.json`       | L3 changes + rollback commands               |
| `optimization_papers.json`  | Full literature search results               |

All artifact files above live under the current iteration's own `opt<n>/`
run directory (`opt<n>/source/`, `opt<n>/patches/`, `opt<n>/scripts/`,
`opt<n>/traces/{raw,compact}/`) — never a hand-built path like
`ws/opt_results/`. **Strict rule (see dftracer-cheatsheet S0):** before
starting a new iteration and again after applying L1/L2/L3 changes, call
`session_validate_structure(run_id=RUN_ID)`; if `clean=false`, call
`session_reorganize_structure(run_id=RUN_ID, dry_run=False)` before
re-running the smoke test or collecting traces for that iteration.


## Built-in Citations

These references are always available and must be used when their coverage matches the bottleneck being addressed.

### GLANCED-IO (Sinurat et al., HPDC 2026)

- **Authors:** Sinurat et al. (Argonne, U. Chicago, LLNL)
- **Title:** "GLANCED-IO: Taming I/O Optimization for Deep Learning at Scale"
- **Venue:** HPDC '26
- **Local PDF:** `resources/papers/HPDC26_GLANCED_IO.pdf` (search via `session_search_local_papers`)
- **Covers:** compute/IO overlap verification, num_workers → prefetch_factor → dataset_access_pattern → PFS_striping → transfer_size (portable-to-hard parameter ordering), cross-layer siloed-optimization pitfalls
- **Key rule (OVERLAP-1):** verify dataloader and compute events actually overlap in the dftracer timeline — sequential load→compute→load (low overlap %) signals a pipeline stall even when num_workers/prefetch_factor look correctly configured; the root cause is often a lower layer (small-I/O access pattern, wrong filesystem, unmatched striping), not the app-level DataLoader knobs

### WisIO (Yildirim et al., ICS 2025)

- **Authors:** Izzet Yildirim, Hariharan Devarajan, Anthony Kougkas, Xian-He Sun, Kathryn Mohror
- **Title:** "WisIO: Automated I/O Bottleneck Detection with Multi-Perspective Views for HPC Workflows"
- **Venue:** Proceedings of the 39th ACM International Conference on Supercomputing (ICS 2025), pp. 749–763
- **URL:** https://dl.acm.org/doi/10.1145/3721145.3730395
- **Covers:** small_io, rand/seq (sequentiality), read_time, write_time, metadata_time, fetch_pressure, epoch_straggler

### Drishti (Bez et al., PDSW 2022)

- **Authors:** Jean Luca Bez, Hammad Ather, Suren Byna
- **Title:** "Drishti: Guiding end-users in the I/O optimization journey"
- **Venue:** 2022 IEEE/ACM International Parallel Data Systems Workshop (PDSW), pp. 1–6. IEEE, 2022
- **URL:** https://ieeexplore.ieee.org/document/10027503
- **Covers:** small-io buffering, metadata caching, sequentiality hints, collective I/O (maps to L1/L2/L3 suggestion layers)


## Metric to Optimization Goal Mapping

Use this table to translate the chosen metric into the dftracer analysis fields that must improve:

| Metric        | Goal                        | Target dftracer fields                                          |
|---------------|-----------------------------|-----------------------------------------------------------------|
| `time`        | Minimize I/O latency        | `read_time_pct`, `write_time_pct`, `metadata_time_pct`         |
| `bandwidth`   | Maximize throughput         | `read_bw_mean`, `write_bw_mean`, `seq_pct`                     |
| `iops`        | Maximize I/O operations/sec | `small_io_pct`, `rand_pct`, `intensity_mean`                   |
| `metadata_ops`| Reduce metadata overhead    | `metadata_time_pct`, `metadata_time_frac_parent`               |

When multiple bottleneck scores are >= 4, default to `time` as the most general metric that addresses all dimensions.


## L1 Application Strategies

These are source-code-level changes (applied under `<WS>/annotated/`). No system or middleware config changes at this layer.

### small_io / small_read / small_write  (metric: bandwidth or iops)

- Coalesce small reads into a single larger read with a staging buffer
- Replace per-element writes with batch writes to a pre-allocated buffer
- Use `readv`/`writev` (scatter/gather) instead of loops of `read`/`write`
- For Python: increase `chunk_size` in file iteration; use `np.fromfile` over loops

### rand / low seq_pct  (metric: bandwidth or time)

- Reorder access so items are read in file-offset order (sort indices before I/O)
- Add `posix_fadvise(fd, 0, 0, POSIX_FADV_SEQUENTIAL)` before sequential reads
- Use `mmap` for random access patterns to let the OS handle page faults
- For deep learning: sort dataset by file size, shuffle only indices in memory

### read_time / write_time  (metric: time)

- Pre-open files and keep file descriptors open across iterations
- Use `O_DIRECT` for cache-bypassing when data reuse is low
- Add async I/O: `io_uring` (C/C++) or `aiofiles`/`asyncio` (Python)
- Pre-allocate file size with `fallocate` before writing to avoid fragmentation

### metadata_time  (metric: metadata_ops)

- Cache `stat()` results; avoid repeated `lstat`/`stat` on the same paths
- Open files once per epoch, not once per sample
- Replace `readdir()` loops with pre-built file-list caches
- For Python: replace `os.path.exists()` in hot loops with `try`/`except`

### fetch_pressure / epoch_straggler  (metric: time or bandwidth — deep learning)

- Increase DataLoader `num_workers` (recommend: `cpu_count // 2`)
- Set `prefetch_factor=4` (PyTorch >= 1.7) or use `tf.data.AUTOTUNE`
- Pre-fetch next batch while compute runs the current one
- Use `persistent_workers=True` to avoid worker respawn overhead

### intensity / checkpoint  (metric: time)

- Write checkpoints asynchronously (background thread or process)
- Use memory-mapped files for checkpoint buffers before flushing to disk
- Compress checkpoints in-memory before writing (lz4 is fast enough)


## L2 Software/Middleware Strategies

These are configuration-level changes: environment variables, library hint files, and runtime config. No source code changes. All changes must be reversible (document the original value).

### MPI-IO / ROMIO  (bottleneck: small_io or rand_pct high — metric: bandwidth or time)

- Enable collective buffering: `ROMIO_HINTS=cb_buffer_size=67108864;romio_cb_read=enable`
- Set data sieving: `romio_ds_read=enable;romio_ds_write=enable`
- Increase aggregator count: `cb_nodes=<num_nodes>`
- Write a hints file at `<WS>/romio_hints.txt` and set the `ROMIO_HINTS` env var to its path

### HDF5  (bottleneck: small_io high — metric: bandwidth or iops)

- Set chunk dimensions to align with collective I/O buffer (e.g. 1 MB chunks)
- Enable HDF5 collective metadata: `H5Pset_coll_metadata_write`, `H5Pset_all_coll_metadata_ops`
- Set chunk cache: `H5Pset_cache(fapl, 0, 521, 64*1024*1024, 1.0)`
- For parallel HDF5: set PHDF5 alignment (`H5Pset_alignment`) to stripe size

### HDF5 metadata  (bottleneck: metadata_time high — metric: metadata_ops)

- Disable HDF5 metadata cache evictions during write-heavy phases
- Batch attribute writes with `H5Pset_object_track_times(False)`

### NetCDF / PnetCDF

- Set `nc_set_default_format(NC_FORMAT_NETCDF4_CLASSIC)` for better performance
- Enable collective I/O: `nc_var_par_access(ncid, varid, NC_COLLECTIVE)`

### PyTorch / Deep Learning  (bottleneck: fetch_pressure or small_io high — metric: time or bandwidth)

- DataLoader: `num_workers = cpu_count // 2`, `prefetch_factor = 4`
- DataLoader: `pin_memory = True` (when GPU is present)
- DataLoader: `persistent_workers = True` (avoids respawn per epoch)
- For datasets: use `worker_init_fn` to pre-open file handles per worker
- `PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:128` (reduces alloc fragmentation)

### PyTorch epoch stragglers  (bottleneck: epoch_straggler high — metric: time)

- Sort dataset by sample size before training to reduce tail latency per batch
- Use `DistributedSampler` with `shuffle=False`; pre-shuffle indices once

### PyTorch checkpoints  (bottleneck: checkpoint high — metric: time)

- `torch.save` with `_use_new_zipfile_serialization=False` for faster writes
- Async checkpoint: `torch.save` in a separate thread
- Set `TORCH_HOME` to a fast local scratch directory

### TensorFlow

- `tf.data.AUTOTUNE` for prefetch buffer sizes
- `tf.data.experimental.AUTOTUNE` for interleave `cycle_length`
- Set `TF_CUDNN_USE_AUTOTUNE=1`, `TF_GPU_THREAD_MODE=gpu_private`

### General I/O libraries  (bottleneck: rand_pct high — metric: bandwidth)

- Set POSIX readahead: `POSIX_FADV_SEQUENTIAL` via `posix_fadvise` (env-level wrapper)
- Configure library-level read-ahead buffer (if applicable)

### Environment variable wrapper script (general)

- `OMP_NUM_THREADS`, `MKL_NUM_THREADS`: set to physical core count for I/O threading
- `MALLOC_ARENA_MAX=2`: reduce glibc arena fragmentation for I/O-heavy apps
- `HBWMALLOC_POLICY=transparent`: use high-bandwidth NUMA memory when available
- Collect all L2 env vars in `<WS>/opt_l2_env.sh`; source before every run


## L3 OS/Filesystem Strategies

These are OS- and filesystem-level tuning changes. Many require `sudo` or storage-admin access. Every proposal MUST include a rollback command and a privilege classification (`no-sudo | sudo | admin-only`).

**MANDATORY: Before proposing any L3 strategy, confirm the detected FS_TYPE
(see Step 8-PRE of [[dftracer-pipeline]]). Only propose strategies whose
"Valid FS_TYPE" column matches the detected filesystem. Strategies for the
wrong filesystem are silently harmful — e.g., `romio_ds_write=disable` causes
a 4× write regression on VAST NVMe but is safe on Lustre spinning disk.**

### Lustre parallel filesystem  (FS_TYPE: lustre only — bottleneck: any_io or bandwidth — metric: bandwidth or iops)

- **Stripe count** (no sudo, per-directory):
  `lfs setstripe -c <N> <WS>/traces/`
  Rollback: `lfs setstripe -c 1 <WS>/traces/`
  Recommendation: N = OST count, capped at 8 for files < 1 GB; 16+ for very large files
- **Stripe size** (no sudo):
  `lfs setstripe -S 4m <WS>/traces/`  (4 MB aligns to ROMIO collective buffer)
  Rollback: `lfs setstripe -S 1m <WS>/traces/`
- **OST pool** (no sudo, if NVMe/SSD pool available):
  `lfs setstripe --pool flash <WS>/`
- **Per-file stripe before write** (no sudo):
  `lfs setstripe -c 4 -S 4m <WS>/traces/<file>` before writing

### Lustre metadata  (FS_TYPE: lustre only — bottleneck: metadata_time high — metric: metadata_ops)

- Disable file creation time tracking: `lfs setstripe --mdt-count 1` (for small dirs)
- Use DNE (Distributed Namespace): `lfs mkdir -c <N>` (admin-only)
- Client-side: mount with `-o localflock` to reduce lock traffic (requires remount — admin)

### Near-node flash accelerators (Rabbit)  (FS_TYPE: any network PFS — bottleneck: read_time/write_time/bandwidth from network-Lustre latency — metric: bandwidth or time)

On systems with node-local NVMe accelerators, stage hot data onto a per-node or
per-chassis flash tier instead of accessing network Lustre directly. No sudo —
provisioned per job via the scheduler.

- **Tuolumne (Rabbit):** request via Flux `-S "#DW jobdw type=<xfs|gfs2|lustre> ..."`.
  Pick the tier by data-sharing scope (SHM ≤20% node mem → XFS single-node ≤1 TB
  → GFS2 across ≤16 nodes on one chassis with `--coral2-chassis=1` → Lustre for
  >16 nodes). Stage input onto `$DW_JOB_*` once, run against it, copy persistent
  outputs back to Lustre at job end. The `#DW` directive is an **allocation-time**
  flag — ask the user to `flux alloc` with it and report the JOBID before you can
  proxy in. Full guide: [[system-tuolumne-rabbit]].


### Linux kernel readahead  (FS_TYPE: local_nvme, local_hdd only — bottleneck: read_time or seq_pct low — metric: time or bandwidth)

- **Per-device** (sudo):
  `sudo blockdev --setra <KB> /dev/<dev>`
  Default: 128 (64 KB); recommended: 4096–16384 (2–8 MB)
  Rollback: `sudo blockdev --setra 128 /dev/<dev>`
  Side effect: affects all processes reading from this device
- **Per-process** (no sudo — cross-layer L1 reinforcement):
  `posix_fadvise(fd, 0, 0, POSIX_FADV_SEQUENTIAL)` in source code
- **Persistent** (sudo): `/etc/udev/rules.d/60-readahead.rules`

### VM and page cache tuning  (FS_TYPE: local_nvme, local_hdd — bottleneck: write_time or checkpoint high — metric: time)

- `sudo sysctl -w vm.dirty_ratio=20`
  (flush dirty pages when 20% of RAM is dirty; tune to 10–40)
  Rollback: `sudo sysctl -w vm.dirty_ratio=20`
- `sudo sysctl -w vm.dirty_background_ratio=5`
  (start background flush at 5%; reduces write stalls; default 10)
- `sudo sysctl -w vm.dirty_expire_centisecs=3000` (30 s dirty page lifetime)
- `sudo sysctl -w vm.dirty_writeback_centisecs=500` (flush every 5 s)
- Side effect: affects all dirty-page behavior on the node

### VM page cache (read-heavy)  (FS_TYPE: local_nvme, local_hdd — bottleneck: read_time high, data fits in RAM)

- `sudo sysctl -w vm.vfs_cache_pressure=50`
  (reduce kernel tendency to reclaim page cache; default 100)
  Rollback: `sudo sysctl -w vm.vfs_cache_pressure=100`

### I/O scheduler  (FS_TYPE: local_nvme, local_hdd only — bottleneck: rand_pct high on SSD/NVMe — metric: iops or time)

- Check current scheduler: `cat /sys/block/<dev>/queue/scheduler`
- For NVMe (no scheduler overhead): `echo none | sudo tee /sys/block/<dev>/queue/scheduler`
- For HDD: `echo mq-deadline | sudo tee /sys/block/<dev>/queue/scheduler`
- Rollback: `echo <original_scheduler> | sudo tee /sys/block/<dev>/queue/scheduler`

### NUMA memory binding  (FS_TYPE: all — bottleneck: fetch_pressure or intensity high — metric: time or bandwidth)

- Check topology: `numactl --hardware`
- Pin process to NUMA node with local memory (no persistent side effect):
  `numactl --cpunodebind=0 --membind=0 <run_command>`
- For MPI: `mpirun --map-by numa:pe=<cores_per_node>` (OpenMPI)

### Network filesystems (NFS, GPFS, BeeGFS)  (FS_TYPE: nfs | gpfs | beegfs only — bottleneck: read_time or metadata_time high)

These typically require storage admin action — surface as MANUAL RECOMMENDATIONS only.
Do NOT apply these to lustre, vast, or local filesystems.
- NFS: mount with `rsize=1048576,wsize=1048576,async`
- GPFS: `mmchattr -r pagePool=<size>` (admin tool)
- BeeGFS: `beegfs-ctl --settuning --clientNumWorkerThreads=16` (admin)


## Citation Rule

Every optimization proposal presented to the user MUST include a verifiable citation with a URL (arXiv link, DOI, or stable webpage). Follow this search order for each unique bottleneck type:

1. Check `PAPERS` from `optimization_context.json` first (already fetched during pipeline setup).
2. Search arXiv: `dftracer__search_arxiv(query="<bottleneck_type> optimization HPC parallel I/O MPI", max_results=5, category="cs.DC")`
3. Search Semantic Scholar / combined: `dftracer__search_papers_combined(query="<bottleneck_type> I/O tuning parallel filesystem", max_results=5)`
4. For any promising arXiv hit, fetch the full record: `dftracer__get_arxiv_paper(paper_id="<arxiv_id>")`

For every citation record:
- authors, title, venue/journal, year
- URL: arXiv link, DOI, or page URL (e.g. `https://arxiv.org/abs/XXXX.XXXXX`)
- the specific finding or section that supports the proposed change (1–2 sentences)

**If no verifiable citation with a URL can be found for a proposal, that proposal MUST be skipped.** Do not present uncited proposals to the user. Instead, write "no L\<N\> fix — no citation available" for that bottleneck.
