#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

cd ${WS}/tmp
module load python/3.13.2 2>&1 | grep -v "Already loaded"
module load cray-mpich/9.0.1 2>&1 | grep -v "Already loaded"
export PATH="${WS}/install/venv/bin:$PATH"
echo "=== Multi-Node 2-Rank DDP Test via torchrun ==="
timeout 180 flux proxy f3NZCB1FchcT flux run -N 2 -n 2 torchrun --nproc-per-node=1 test_ddp_torchrun.py
