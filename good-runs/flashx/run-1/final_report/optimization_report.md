# Flash-X Sedov 3D Baseline Analysis & Optimization Report

## Executive Summary

The Flash-X Sedov 3D production baseline run completed successfully with:
- **5,867 timesteps** reaching t=0.4999 (target tmax=0.5)
- **~5.5 minutes** total runtime
- **18 checkpoints** written, totaling ~6.5GB
- **27.5M trace events** captured across 6,185 trace files

## Critical Finding: Small I/O Bottleneck

The dominant performance issue is **extremely small write operations** during checkpointing:

| Metric | Value |
|--------|-------|
| Total POSIX write operations | **24,119,080** (87.6% of all events) |
| Mean write duration | **13-17 microseconds** |
| Total POSIX I/O time | **846 seconds** |
| Compute time | **60.8 seconds** |
| I/O overhead ratio | **13.9x compute time** |

### Top 4 checkpoint files (by fhash):
| File Hash | Write Operations | Opens |
|-----------|------------------|-------|
| ad3a790caf9efe80 | 6,432,050 | 32,256 |
| 8fe7c1b466761c18 | 6,387,660 | 32,256 |
| acb0ce183ba5d2e6 | 5,961,772 | 32,256 |
| 7943193de8ec2ee4 | 5,336,056 | 32,256 |

These 4 files account for **~24.1M writes** — essentially ALL writes in the trace.

## Diagnosed Bottlenecks

### 1. CRITICAL: Small I/O Aggregation Failure
- **Symptom**: 24.1M individual write() calls with mean size likely in bytes to hundreds of bytes
- **Root Cause**: Flash-X HDF5 checkpointing writes each variable/block separately without buffering
- **Impact**: 846s of I/O time vs 61s of compute — **93% overhead**

### 2. HIGH: Frequent Checkpointing
- **Symptom**: 18 checkpoints for 0.5 time units (checkpointFileIntervalTime=0.03)
- **Root Cause**: Aggressive checkpoint interval for short simulation
- **Impact**: Checkpoint I/O dominates runtime

### 3. MEDIUM: Metadata Overhead
- **Symptom**: 165,540 open + 145,008 close operations
- **Root Cause**: Each checkpoint creates new files with many variables
- **Impact**: ~4.1s in open() operations alone

## Optimization Proposals

### Level 1: Application Code Changes

#### 1.1 Increase HDF5 Chunk Size and Enable Collective I/O
**File**: `source/object/flash_production.par`
**Change**:
```
# Current (implicit defaults)
# Recommended:
checkpointFileIntervalStep = 1000    # Checkpoint every N steps instead of time
```

Also modify I/O implementation in `IO/IOMain/hdf5/`:
- Set HDF5 chunk size to match Lustre stripe size (1MB)
- Enable H5Pset_coll_metadata_write for collective metadata

#### 1.2 Buffer Small Writes in Flash-X I/O
**File**: `source/IO/IOMain/hdf5/io_write_data.F90` or equivalent
**Strategy**: Aggregate small per-block writes into larger buffers before calling HDF5

**Expected Impact**: 10-100x reduction in write() syscall count

### Level 2: Software/Middleware Configuration

#### 2.1 HDF5 Tuning Environment Variables
```bash
export HDF5_MPI_OPT_RANGE=1          # Enable MPI optimizations
export HDF5_USE_FILE_LOCKING=FALSE     # Disable file locking on Lustre
export HDF5_COLL_METADATA_WRITE=1      # Collective metadata writes
```

#### 2.2 Lustre Stripe Configuration
```bash
# Set before running:
lfs setstripe -c 8 -s 1M /p/lustre5/haridev/flashx/baseline_production/
```
- **-c 8**: Stripe across 8 OSTs (matches 8 nodes)
- **-s 1M**: 1MB stripe size (matches HDF5 chunk size)

#### 2.3 Increase Checkpoint Interval
**File**: `flash_production.par`
```
checkpointFileIntervalTime = 0.1    # Increase from 0.03
# OR use step-based:
checkpointFileIntervalStep = 500
```

**Expected Impact**: 3x fewer checkpoints → 3x less I/O time

### Level 3: Filesystem/OS Tuning

#### 3.1 Linux I/O Tuning
```bash
# Increase writeback buffers
sysctl -w vm.dirty_ratio=40
sysctl -w vm.dirty_background_ratio=10

# Increase readahead
blockdev --setra 8192 /dev/???  # Device for Lustre mount
```

#### 3.2 Lustre Client Tuning
```bash
# Increase RPC size
lctl set_param osc.*.max_pages_per_rpc=256

# Enable writeback caching
lctl set_param llite.*.writethrough=0
```

## Expected Performance Improvements

| Optimization | Expected Improvement | Effort |
|-------------|---------------------|--------|
| L1: Buffer writes | 10-50x fewer syscalls | Medium |
| L2: Lustre striping | 2-4x bandwidth | Low |
| L2: Checkpoint interval | 3x fewer checkpoints | Low |
| L3: Writeback tuning | 1.5-2x throughput | Low |
| **Combined** | **5-20x faster I/O** | **Medium** |

## References

1. [Checkpoint/Restart for Lagrangian particle mesh with AMR in FLASH-X](https://arxiv.org/abs/2103.04267v1) — Jain et al., 2021
2. [Development of a Burst Buffer System for Data-Intensive Applications](https://arxiv.org/abs/1505.01765v1) — Wang et al., 2015
3. [Understanding LLM Checkpoint/Restore I/O Strategies](https://arxiv.org/abs/2512.24511v1) — Gossman et al., 2025

## Next Steps

1. Apply L2 optimizations first (low effort, immediate impact)
2. Modify checkpoint interval in flash.par
3. Set Lustre striping on checkpoint directory
4. Re-run with dftracer to measure improvement
5. If still bottlenecked, implement L1 write buffering in Flash-X I/O layer
