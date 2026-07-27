#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

set -e
WS=${WS}
. "$WS/scripts/env.sh"
. "$WS/install/venv/bin/activate"
export ROCM_HOME=/opt/rocm-6.4.3
export ROCM_PATH=/opt/rocm-6.4.3
export LD_LIBRARY_PATH="$WS/install/venv/lib/python3.13/site-packages/torch/lib:${LD_LIBRARY_PATH}"
export DFTRACER_ENABLE=0
python "$WS/tmp/make_slice.py"
