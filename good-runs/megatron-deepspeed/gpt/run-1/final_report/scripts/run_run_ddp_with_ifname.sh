#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

cd ${WS}/tmp
module load python/3.13.2 2>&1 | grep -v "Already loaded"
module load cray-mpich/9.0.1 2>&1 | grep -v "Already loaded"
export MASTER_ADDR="tuolumne1158"
export MASTER_PORT="6000"
export GLOO_SOCKET_IFNAME="hsi0"
export PATH="${WS}/install/venv/bin:$PATH"
echo "=== 2-Rank DDP Test with HSN Interface (hsi0) ==="
timeout 180 flux proxy f3NZCB1FchcT flux run -N 2 -n 2 --env MASTER_ADDR=$MASTER_ADDR --env MASTER_PORT=$MASTER_PORT --env GLOO_SOCKET_IFNAME=$GLOO_SOCKET_IFNAME python3 test_ddp_with_ifname.py
