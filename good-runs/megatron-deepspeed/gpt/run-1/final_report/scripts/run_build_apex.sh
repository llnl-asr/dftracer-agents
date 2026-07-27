#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

set -e
WS=${WS}
source "$WS/scripts/env.sh"
source "$WS/install/venv/bin/activate"
export ROCM_HOME=/opt/rocm-6.4.3
export ROCM_PATH=/opt/rocm-6.4.3
export PATH="$ROCM_HOME/bin:$PATH"
export PYTORCH_ROCM_ARCH=gfx942
export CC=/opt/cray/pe/mpich/9.0.1/ofi/crayclang/20.0/bin/mpicc
export CXX=/opt/cray/pe/mpich/9.0.1/ofi/crayclang/20.0/bin/mpicxx
cd "$WS/build/apex"
pip install -v --no-build-isolation . 2>&1
