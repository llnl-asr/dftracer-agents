#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

set -e
export DFTRACER_ENABLE=1
export DFTRACER_INC_METADATA=1
export DFTRACER_LOG_FILE=${WS}/ygm_bench_4n/traces/raw/ygm_bench_4n-21ed5512
export DFTRACER_DATA_DIR=all
export DFTRACER_INIT=FUNCTION
export YGM_COMM_IRECVS_SIZE_KB=8192
export YGM_COMM_NUM_IRECVS=4
export LD_LIBRARY_PATH='/opt/cray/pe/lib64:/opt/cray/pe/mpich/9.0.1/ofi/gnu/11.2/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib/default64:/usr/lib64:${WS}/venv-src/lib/python3.13/site-packages/dftracer/lib64:$LD_LIBRARY_PATH'
cd ${WS}/ygm-bench/build/src
./barrier_bench -b 500000 -a 0 -t 1 -p
