#!/bin/bash
set -e
WS=$PROJECT_ROOT/workspaces/vpic_kokkos/20260714_155730
source $WS/tmp/env_tuolumne.sh
export CC=$(which mpicc)
export CXX=$(which mpic++)

RUN_NAME=${1:-validate8node_benchmark_omp}
NNODES=8
NTASKS=128   # matches benchmark.cxx's fixed 8x4x4 domain decomposition
CORES_PER_TASK=6  # 96 cores/node / 16 ranks-per-node = 6

mkdir -p $WS/traces/$RUN_NAME $WS/dataset/$RUN_NAME

DFTRACER_LIB=$WS/venv/lib/python3.13/site-packages/dftracer/lib64
FULL_LD_PATH="/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib/default64:/usr/lib64:${DFTRACER_LIB}:${LD_LIBRARY_PATH}"

cd $WS/dataset/$RUN_NAME
flux run -N$NNODES -n$NTASKS -c$CORES_PER_TASK \
  --env "LD_LIBRARY_PATH=${FULL_LD_PATH}" \
  --env DFTRACER_ENABLE=1 \
  --env DFTRACER_INIT=FUNCTION \
  --env DFTRACER_INC_METADATA=1 \
  --env DFTRACER_DATA_DIR=all \
  --env "DFTRACER_LOG_FILE=$WS/traces/$RUN_NAME/vpic-$RUN_NAME" \
  --env OMP_NUM_THREADS=$CORES_PER_TASK \
  --env OMP_PROC_BIND=spread \
  --env OMP_PLACES=cores \
  $WS/dataset/validate8node_benchmark/benchmark.Linux
RUN_EXIT=$?

echo "RUN_EXIT=$RUN_EXIT"
