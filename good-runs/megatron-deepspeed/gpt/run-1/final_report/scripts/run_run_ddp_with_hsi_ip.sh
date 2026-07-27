#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

cd ${WS}/tmp
module load python/3.13.2 2>&1 | grep -v "Already loaded"
module load cray-mpich/9.0.1 2>&1 | grep -v "Already loaded"

# Get the HSN IP of the first node (rank 0 master)
echo "Getting HSN IP of first node..."
export MASTER_ADDR=$(timeout 30 flux proxy f3NZCB1FchcT flux run -N 1 -n 1 --requires=hosts:tuolumne1158 bash -c "ip addr show hsi0 | grep 'inet ' | awk '{print \$2}' | cut -d/ -f1" 2>&1 | grep "192.168.1")

echo "Using MASTER_ADDR=$MASTER_ADDR (HSN IP)"

export MASTER_PORT="6000"
export GLOO_SOCKET_IFNAME="hsi0"
export PATH="${WS}/install/venv/bin:$PATH"

echo "=== 2-Rank DDP Test with HSN IP Address ==="
timeout 180 flux proxy f3NZCB1FchcT flux run -N 2 -n 2 --env MASTER_ADDR=$MASTER_ADDR --env MASTER_PORT=$MASTER_PORT --env GLOO_SOCKET_IFNAME=$GLOO_SOCKET_IFNAME python3 test_ddp_with_ifname.py
