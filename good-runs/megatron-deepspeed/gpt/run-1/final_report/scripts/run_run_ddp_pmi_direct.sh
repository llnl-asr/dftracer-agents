#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

set -e

export MASTER_ADDR="tuolumne1158"
export MASTER_PORT="6000"

# Set up environment (modules, LD_LIBRARY_PATH, etc.)
module load python/3.13.2
module load cray-mpich/9.0.1

# Add venv to PATH
export PATH="${WS}/install/venv/bin:$PATH"

echo "=== 2-Rank DDP Test with PMI-derived Env ==="
echo "MASTER_ADDR=$MASTER_ADDR, MASTER_PORT=$MASTER_PORT"
echo "PATH=$PATH" | head -c 100
echo ""

cd ${WS}/tmp

timeout 180 flux proxy f3NZCB1FchcT flux run \
  -N 2 -n 2 \
  --env MASTER_ADDR=$MASTER_ADDR \
  --env MASTER_PORT=$MASTER_PORT \
  python3 test_ddp_with_pmi.py

echo ""
echo "=== DDP test completed ==="
