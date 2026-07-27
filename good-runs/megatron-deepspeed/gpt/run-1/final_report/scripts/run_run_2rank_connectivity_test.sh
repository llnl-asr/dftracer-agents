#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

set -e

export MASTER_ADDR="tuolumne1158"
export MASTER_PORT="6000"

echo "=== Running 2-rank TCP connectivity test ==="
echo "MASTER_ADDR=$MASTER_ADDR, MASTER_PORT=$MASTER_PORT"
echo ""

cd ${WS}/tmp

timeout 120 flux proxy f3NZCB1FchcT flux run \
  -N 2 -n 2 \
  --env MASTER_ADDR=$MASTER_ADDR \
  --env MASTER_PORT=$MASTER_PORT \
  python3 test_ddp_connect.py

echo ""
echo "=== Connectivity test completed ==="
