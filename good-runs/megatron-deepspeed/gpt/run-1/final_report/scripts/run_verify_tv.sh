#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

set -e
WS=${WS}
source "$WS/scripts/env.sh"
source "$WS/install/venv/bin/activate"
python -c "import torch; import torchvision; print(torch.__version__, torchvision.__version__)"
