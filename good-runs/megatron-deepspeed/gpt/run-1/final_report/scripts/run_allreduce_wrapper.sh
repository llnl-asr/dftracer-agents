#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

WS="${WS}"
ml load cce/20.0.0 cray-mpich/9.0.1 rocm/6.4.3 rccl/working-env >/dev/null 2>&1
export LD_LIBRARY_PATH="/opt/rocm-6.4.3/lib:/opt/cray/pe/mpich/9.0.1/ofi/gnu/11.2/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib/default64:/usr/lib64:${LD_LIBRARY_PATH}"
source "$WS/install/venv/bin/activate"
export LD_LIBRARY_PATH="$WS/install/venv/lib/python3.13/site-packages/torch/lib:${LD_LIBRARY_PATH}"
export LD_LIBRARY_PATH="/collab/usr/global/tools/rccl/toss_4_x86_64_ib_cray/rocm-6.4.1/install/lib:${LD_LIBRARY_PATH}"
export NCCL_CROSS_NIC=1
export FI_MR_CACHE_MONITOR=userfaultfd
export NCCL_SOCKET_IFNAME=hsi
export RANK="${FLUX_TASK_RANK}"
export WORLD_SIZE=4
export LOCAL_RANK=0
export MASTER_ADDR="$(flux hostlist local | hostlist -n 1)"
export MASTER_PORT=29513
export NCCL_DEBUG=WARN
exec python3 "$WS/tmp/allreduce_test.py"
