#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

set -e
cd ${WS}/build
${WS}/source/configure \
  --prefix=${WS}/install \
  --with-hdf5=/usr \
  --with-mpiio \
  --disable-dependency-tracking
echo "Configure complete"
