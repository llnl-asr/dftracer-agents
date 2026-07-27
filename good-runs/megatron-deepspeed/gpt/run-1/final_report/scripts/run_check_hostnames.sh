#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

set -e

echo "=== Checking hostname resolution from compute nodes ==="
echo "Expected MASTER_ADDR nodes: tuolumne[1158,1161,1163,1220]"

# Get the first node in the allocation to use as MASTER_ADDR
MASTER_ADDR=$(flux proxy f3NZCB1FchcT flux run -N 1 -n 1 --requires=hosts:tuolumne1158 hostname)
echo "Node tuolumne1158 resolves to: $MASTER_ADDR"

# Now check if each node can resolve that same hostname
for node in tuolumne1158 tuolumne1161 tuolumne1163 tuolumne1220; do
  echo ""
  echo "From $node:"
  
  # Check getent hosts resolution
  RESOLVED=$(flux proxy f3NZCB1FchcT flux run -N 1 -n 1 --requires=hosts:$node \
    bash -c "getent hosts $MASTER_ADDR 2>/dev/null || echo 'NOT_RESOLVED'" 2>&1 | tail -1)
  echo "  getent hosts $MASTER_ADDR -> $RESOLVED"
  
  # Also try nslookup if available
  IP=$(flux proxy f3NZCB1FchcT flux run -N 1 -n 1 --requires=hosts:$node \
    bash -c "python3 -c 'import socket; print(socket.gethostbyname(\"$MASTER_ADDR\"))' 2>/dev/null || echo 'NSLOOKUP_FAILED'" 2>&1 | tail -1)
  echo "  python3 socket.gethostbyname($MASTER_ADDR) -> $IP"
done
