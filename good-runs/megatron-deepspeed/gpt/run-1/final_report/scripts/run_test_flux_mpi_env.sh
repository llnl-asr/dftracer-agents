#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

set -e

echo "=== Testing Flux MPI Environment Setup ==="

echo ""
echo "Test 1: Plain flux run (no MPI)"
timeout 30 flux proxy f3NZCB1FchcT flux run -N 1 -n 1 \
  bash -c 'echo "RANK=$RANK, PMI_RANK=$PMI_RANK"' 2>&1 | head -5

echo ""
echo "Test 2: flux run with MPICH environment"
timeout 30 flux proxy f3NZCB1FchcT flux run -N 1 -n 1 \
  bash -c 'env | grep -E "^(RANK|PMI|WORLD|LOCAL|PALS)" | head -20' 2>&1

echo ""
echo "Test 3: Run MPI program through Flux"
timeout 30 flux proxy f3NZCB1FchcT flux run -N 2 -n 2 \
  bash -c 'if [ -z "$PMI_RANK" ]; then echo "No PMI env set"; fi; env | grep -E "^(RANK|PMI|WORLD|LOCAL|PALS)" | sort | uniq' 2>&1 | sort -u

echo ""
echo "Done with environment tests"
