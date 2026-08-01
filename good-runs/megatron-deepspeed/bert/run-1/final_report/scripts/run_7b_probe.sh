#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

WS=${WS}
source $WS/scripts/env.sh
cd $WS
python3 tmp/7b_probe.py
