#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

set -e
export DFTRACER_ENABLE=1
export DFTRACER_INC_METADATA=1
export DFTRACER_LOG_FILE=${WS}/ygm_bench/traces/raw/ygm_bench-fe4c81ed
export DFTRACER_DATA_DIR=all
export DFTRACER_INIT=FUNCTION
export LD_LIBRARY_PATH='/opt/cray/pe/lib64:/opt/cray/pe/mpich/9.0.1/ofi/gnu/11.2/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib/default64:/usr/lib64:${WS}/venv-311/lib/python3.11/site-packages/dftracer/lib64:${WS}/venv-311/lib/python3.11/site-packages/dftracer/dftracer.libs:$LD_LIBRARY_PATH'
cd ${WS}/ygm-bench/build/src
./around_the_world_ygm -n 50 -t 2 -p && ./barrier_bench -b 500 -a 0 -t 2 -p
