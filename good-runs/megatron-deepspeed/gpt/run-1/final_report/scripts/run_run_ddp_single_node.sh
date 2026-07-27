#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

cd ${WS}/tmp
module load python/3.13.2 2>&1 | grep -v "Already loaded"
module load cray-mpich/9.0.1 2>&1 | grep -v "Already loaded"
export MASTER_ADDR="localhost"
export MASTER_PORT="6000"
export PATH="${WS}/install/venv/bin:$PATH"
echo "=== Single-Node 2-Rank DDP Test (localhost) ==="
timeout 180 flux proxy f3NZCB1FchcT flux run -N 1 -n 2 --env MASTER_ADDR=$MASTER_ADDR --env MASTER_PORT=$MASTER_PORT python3 test_ddp_single_node.py
