#!/bin/bash
set -e
module load craype-x86-trento libfabric/match_SHS craype-network-ofi perftools-base/25.09.0 craype/2.7.35
module swap PrgEnv-cray PrgEnv-gnu 2>&1 || module load PrgEnv-gnu/8.7.0
module load gcc-native/11.2
module load flux_wrappers/0.1 xpmem/2.6.5 cray-libsci/25.09.0 cray-mpich/9.0.1 python/3.13.2

export CC=/opt/cray/pe/mpich/9.0.1/ofi/gnu/11.2/bin/mpicc
export CXX=/opt/cray/pe/mpich/9.0.1/ofi/gnu/11.2/bin/mpicxx

WS=${WS}
DFTRACER_ROOT=$WS/venv-src-gnu/lib/python3.13/site-packages/dftracer

export LD_LIBRARY_PATH=/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib/default64:/usr/lib64:/opt/cray/pe/lib64:/opt/cray/lib64:$DFTRACER_ROOT/lib64:$LD_LIBRARY_PATH

echo "CC=$CC"; $CC --version | head -1
echo "CXX=$CXX"; $CXX --version | head -1
echo "DFTRACER_ROOT=$DFTRACER_ROOT"


cmake -S "$WS/annotated/source" -B "$WS/build_ann" \
  -DCMAKE_INSTALL_PREFIX="$WS/install_ann" \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo \
  -DYGM_BUILD_TESTS=ON \
  -DDFTRACER_ROOT="$DFTRACER_ROOT" \
  -DCMAKE_CXX_STANDARD=20 \
  -DCMAKE_CXX_STANDARD_REQUIRED=ON \
  -DCMAKE_CXX_COMPILER="$CXX" \
  -DCMAKE_C_COMPILER="$CC"

cmake --build "$WS/build_ann" -j8 --target MPI_test_comm
cmake --install "$WS/build_ann" || true
