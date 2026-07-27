#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

WS=${WS}
source "$WS/scripts/env.sh"
source "$WS/install/venv/bin/activate"
pip install torchvision --no-deps 2>&1 | tail -30
