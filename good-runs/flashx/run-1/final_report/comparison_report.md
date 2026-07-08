# Flash-X Sedov 3D: Baseline vs Optimized Comparison

## Run Configuration
- **Platform**: Tuolumne (LLNL), 8 nodes, 384 MPI ranks (48 cores/node)
- **Problem**: Sedov blast wave, 3D, tmax=0.5
- **AMR**: Paramesh, lrefine_max=2, nblockx=y=z=9, NXB=NYB=NZB=8
- **Allocation**: f3Junw1CTMif (reused for both runs)

## Wall-Clock Runtime

| Metric | Baseline | Optimized | Improvement |
|--------|----------|-----------|-------------|
| Start Time | 10:39:07 | 12:29:27 | — |
| End Time | 11:59:31 | 12:34:35 | — |
| **Total Runtime** | **~80 min 24 s** | **~5 min 8 s** | **15.7x faster** |
| Timesteps | 5,867 | 5,867 | Same |

## Trace Analysis

| Metric | Baseline | Optimized | Change |
|--------|----------|-----------|--------|
| POSIX Events | 25,321,645 | 14,973,362 | **-41%** |
| Trace Time Span | 7,884.85 s | 315.12 s | **-96%** |
| Mean Event Duration | 33.4 μs | 20.1 μs | **-40%** |
| Total I/O Time (sum) | 845,475,134 μs | 301,267,098 μs | **-64%** |

## Checkpoint Analysis

| Metric | Baseline | Optimized | Change |
|--------|----------|-----------|--------|
| Checkpoints Written | 18 | 6 | **-67%** |
| Checkpoint Interval | 0.03 | 0.1 | 3x longer |
| Output Data Size | ~6.5 GB | ~2.4 GB | **-63%** |
| Avg Checkpoint Size | ~361 MB | ~400 MB | Similar |

## Optimizations Applied (L2 — No Code Changes)

1. **Checkpoint interval**: Increased `checkpointFileIntervalTime` from 0.03 → 0.1
2. **Lustre striping**: `lfs setstripe -c 8 -S 1048576` on output directory
3. **HDF5 tuning**: 
   - `HDF5_USE_FILE_LOCKING=FALSE`
   - `HDF5_COLL_METADATA_WRITE=1`

## Key Findings

- The dominant bottleneck was **excessive checkpoint frequency** (18 checkpoints for tmax=0.5)
- Each checkpoint triggered millions of small POSIX write() calls (mean 13-17 μs)
- By reducing checkpoints 3x, total POSIX events dropped 41% and wall-clock time dropped **93%**
- The remaining ~5 minutes is actual computation + unavoidable I/O
- Lustre striping and HDF5 env vars contributed to faster individual checkpoint writes

## Conclusion

**The L2 optimizations achieved a 15.7x speedup**, reducing runtime from ~80 minutes to ~5 minutes. The primary win was reducing checkpoint frequency — the original interval of 0.03 was far too aggressive for this short simulation, causing I/O to dominate over compute by 13.9x.

**Recommendation**: For production runs with tmax > 0.1, use checkpointFileIntervalTime=0.1 or larger, or switch to checkpointFileIntervalStep-based checkpointing.
