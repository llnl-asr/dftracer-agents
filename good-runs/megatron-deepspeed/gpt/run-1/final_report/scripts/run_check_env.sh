#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

set -e
WS=${WS}
source "$WS/scripts/env.sh"
source "$WS/install/venv/bin/activate"
python -c "import torch; print(torch.__version__, torch.version.hip)"
which hipcc
hipcc --version 2>&1 | head -3
ls /opt/rocm-6.4.3 2>&1 | head -5
