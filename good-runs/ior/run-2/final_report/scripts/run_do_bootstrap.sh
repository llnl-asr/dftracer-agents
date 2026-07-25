#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

set -e
cd ${WS}/source
autoreconf -fi -I config
echo "Bootstrap complete"
