#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

source /usr/share/lmod/lmod/init/bash
flux run -N 8 -n 512 ${WS}/opt_align1m/scripts/run.sh
