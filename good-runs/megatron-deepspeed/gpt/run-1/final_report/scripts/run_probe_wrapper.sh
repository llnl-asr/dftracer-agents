#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

WS=${WS}
source "$WS/scripts/env.sh" >/dev/null 2>&1
source "$WS/install/venv/bin/activate"
export ROCM_HOME=/opt/rocm-6.4.3 ROCM_PATH=/opt/rocm-6.4.3 PYTORCH_ROCM_ARCH=gfx942
export PATH="/opt/rocm-6.4.3/bin:$PATH"
export LD_LIBRARY_PATH="$WS/install/venv/lib/python3.13/site-packages/torch/lib:/opt/rocm-6.4.3/lib:$LD_LIBRARY_PATH"
export DFTRACER_ENABLE=0
python3 "$WS/tmp/probe_kernels.py"
