#!/bin/bash
# Rebuild the session's dependencies and application from scratch.
# Adjust WS to wherever you want the workspace to live.
set -e
WS="${WS:-$(cd "$(dirname "$0")/../.." && pwd)}"

# 1) HDF5 must be built from source (never the Cray/system module).
#    Expected at $WS/hdf5_1.14 with lib/libhdf5.so* present.
test -f "$WS/hdf5_1.14/lib/libhdf5.so" || {
  echo "ERROR: build+install HDF5 1.14.x into $WS/hdf5_1.14 first"; exit 1; }

# 2) dftracer (MPI + HDF5 on; HIP/ROCm OFF for CPU-only workloads).
#    Installed prefix used by this session:
#      $PROJECT_ROOT/workspaces/h5bench/20260710_061131/venv/lib/python3.13/site-packages/dftracer/lib64

# 3) Application build.
#    Flash-X: the SERIAL HDF5 IO unit is the default and makes
#    useCollectiveHDF5 inert -- always pass +parallelIO.
cd "$WS/annotated/source"
bash setup Sedov -auto -3d +parallelIO

# `setup` regenerates object/ from scratch: re-apply the dftracer build config
# and the constructor/destructor shim AFTER it runs, never before.
cp "$WS/tmp/opt1_backup/Makefile.h"           object/Makefile.h
cp "$WS/tmp/opt1_backup/dftracer_init_fini.c" object/dftracer_init_fini.c

cd object && make -j16
echo "built: $PWD/flashx"
