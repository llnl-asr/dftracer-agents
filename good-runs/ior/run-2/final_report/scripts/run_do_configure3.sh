#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

set -e
cd ${WS}/build
make distclean 2>&1 > /dev/null || true
rm -rf autom4te.cache config.status config.log config.h.in~ 2>/dev/null || true
export CPPFLAGS="-I/usr/include"
export LDFLAGS="-L/usr/lib64"
export LIBS="-lhdf5"
${WS}/source/configure \
  --prefix=${WS}/install \
  --with-hdf5 \
  --with-mpiio \
  --disable-dependency-tracking
echo "Configure complete"
