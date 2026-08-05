#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

source /usr/share/lmod/lmod/init/bash
module load craype-x86-trento libfabric/match_SHS craype-network-ofi perftools-base/25.09.0 craype/2.7.35
module swap PrgEnv-cray PrgEnv-gnu 2>&1 | tail -1
module load gcc-native/11.2 flux_wrappers/0.1 xpmem/2.6.5 cray-libsci/25.09.0 cray-mpich/9.0.1 python/3.13.2
export LD_LIBRARY_PATH=/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib/default64:/usr/lib64:/opt/cray/pe/lib64:${WS}/venv-src-gnu/lib/python3.13/site-packages/dftracer/lib64:$LD_LIBRARY_PATH
export DFTRACER_ENABLE=0
time ${WS}/ygm-bench/build/src/around_the_world_ygm -n 5000 -t 1 -p
