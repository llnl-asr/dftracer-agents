#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

set -e

WS="${WS}"
cd "$WS"

# Load modules (same as build)
module load craype-x86-trento libfabric/match_SHS craype-network-ofi perftools-base/25.09.0 \
  craype/2.7.35 PrgEnv-cray/8.7.0 flux_wrappers/0.1 xpmem/2.6.5 \
  cce/20.0.0 cray-libsci/25.09.0 cray-mpich/9.0.1 python/3.13.2

# Activate venv
source install/venv/bin/activate

# Install hpc-launcher
echo "Installing hpc-launcher==1.0.4..."
pip install hpc-launcher==1.0.4

# Verify torchrun-hpc exists
if [ -f "install/venv/bin/torchrun-hpc" ]; then
  echo "SUCCESS: torchrun-hpc installed at $(which torchrun-hpc)"
  torchrun-hpc --version || echo "Note: torchrun-hpc installed (version check may not be supported)"
else
  echo "ERROR: torchrun-hpc not found in venv!"
  exit 1
fi
