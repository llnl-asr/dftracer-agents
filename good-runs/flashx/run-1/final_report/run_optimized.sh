#!/bin/bash
# OPTIMIZED production run for Flash-X Sedov 3D
# Allocation: f3Junw1CTMif (8 nodes, 48 cores/node = 384 cores total)
#
# OPTIMIZATIONS APPLIED:
#   1. checkpointFileIntervalTime = 0.1 (was 0.03) → 3x fewer checkpoints
#   2. Lustre striping: lfs setstripe -c 8 -s 1M (applied to output dir)
#   3. HDF5 env vars: collective metadata, no file locking
#
# USAGE: flux proxy f3Junw1CTMif flux run -N 8 -n 384 --exclusive \
#          --cwd /usr/WS2/haridev/dftracer-agents/workspaces/flash_x/20260708_063844/annotated/source/object \
#          /usr/WS2/haridev/dftracer-agents/workspaces/flash_x/20260708_063844/run_optimized.sh

set -e

WS="/usr/WS2/haridev/dftracer-agents/workspaces/flash_x/20260708_063844"
FLASHX_DIR="${WS}/annotated/source/object"
LUSTRE_OUT="/p/lustre5/haridev/flashx/baseline_production"

# Environment
export PATH="/usr/WS2/haridev/dftracer-agents/.venv/bin:$PATH"
export LD_LIBRARY_PATH="${WS}/hdf5_1.14/lib:${WS}/install/lib/python3.13/site-packages/dftracer/lib64:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:$LD_LIBRARY_PATH"

# DFTracer setup — PRELOAD mode
export DFTRACER_ENABLE=1
export DFTRACER_INIT=PRELOAD
export DFTRACER_DATA_DIR=all
export DFTRACER_INC_METADATA=1
export DFTRACER_LOG_FILE="${WS}/opt1/traces/raw/optimized"
export LD_PRELOAD="${WS}/install/lib/python3.13/site-packages/dftracer/lib64/libdftracer_preload.so"

# MPI / HDF5 settings
export MPICH_GPU_SUPPORT_ENABLED=0
export HDF5_USE_FILE_LOCKING=FALSE

# HDF5 collective metadata optimization
export HDF5_COLL_METADATA_WRITE=1

# Ensure output directories exist
mkdir -p "${WS}/opt1/traces/raw"
mkdir -p "${LUSTRE_OUT}"

# Copy OPTIMIZED parameter file
cp "${FLASHX_DIR}/flash_optimized.par" "${FLASHX_DIR}/flash.par"

cd "${FLASHX_DIR}"

echo "=== Starting OPTIMIZED production run ==="
echo "Date: $(date)"
echo "Host: $(hostname)"
echo "PWD: $(pwd)"
echo "Checkpoint interval: 0.1 (was 0.03)"
echo "Lustre striping: lfs setstripe -c 8 -s 1M"
echo "HDF5 env: HDF5_USE_FILE_LOCKING=FALSE HDF5_COLL_METADATA_WRITE=1"
echo "LD_PRELOAD: ${LD_PRELOAD}"
echo ""

# Run Flash-X — dftracer PRELOAD is active via LD_PRELOAD
./flashx

EXIT_CODE=$?

echo ""
echo "=== Run completed ==="
echo "Exit code: ${EXIT_CODE}"
echo "End time: $(date)"

# List output files on Lustre
echo "=== Lustre output files ==="
ls -lh "${LUSTRE_OUT}/" || true

# List trace files
echo "=== Trace files ==="
ls -lh "${WS}/opt1/traces/raw/" || true

exit ${EXIT_CODE}
