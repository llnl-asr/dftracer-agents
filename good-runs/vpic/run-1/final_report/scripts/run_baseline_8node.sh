#!/bin/bash
set -e
WS=$PROJECT_ROOT/workspaces/vpic_kokkos/20260714_155730
source $WS/tmp/env_tuolumne.sh
export CC=$(which mpicc)
export CXX=$(which mpic++)

RUN_NAME=${1:-validate8node_benchmark}
NNODES=8
NTASKS=128   # matches benchmark.cxx's fixed 8x4x4 domain decomposition

mkdir -p $WS/traces/$RUN_NAME $WS/dataset/$RUN_NAME

DFTRACER_LIB=$WS/venv/lib/python3.13/site-packages/dftracer/lib64
FULL_LD_PATH="/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib/default64:/usr/lib64:${DFTRACER_LIB}:${LD_LIBRARY_PATH}"

# dftracer_service temporarily disabled: stop path can't find the running
# instance (state file/socket not written under the passed path prefix),
# leaving the daemon stuck running after every job. Re-enable once fixed.

cd $WS/dataset/$RUN_NAME
flux run -N$NNODES -n$NTASKS \
  --env "LD_LIBRARY_PATH=${FULL_LD_PATH}" \
  --env DFTRACER_ENABLE=1 \
  --env DFTRACER_INIT=FUNCTION \
  --env DFTRACER_INC_METADATA=1 \
  --env DFTRACER_DATA_DIR=all \
  --env "DFTRACER_LOG_FILE=$WS/traces/$RUN_NAME/vpic-$RUN_NAME" \
  $WS/dataset/validate8node_benchmark/benchmark.Linux
RUN_EXIT=$?

echo "RUN_EXIT=$RUN_EXIT"
