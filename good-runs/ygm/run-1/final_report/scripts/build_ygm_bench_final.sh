#!/bin/bash
set -e
# GNU toolchain build for ygm-bench against the ANNOTATED YGM tree.
# Same toolchain fix as build_gnu.sh (Cray-clang20 + spdlog-bundled-fmt
# consteval incompatibility -> use PrgEnv-gnu / gcc-native/11.2 / matching
# cray-mpich gnu/11.2 wrapper dir).
#
# Consumption pattern: ygm-bench's top-level CMakeLists.txt does
# find_package(ygm CONFIG) first (which will NOT be found -- the annotated
# YGM tree has no install()/export() rules, confirmed by inspection), then
# falls back to FetchContent_Declare(ygm GIT_REPOSITORY ...)+MakeAvailable.
# We redirect that FetchContent to use our ANNOTATED source directory
# in-place (no git clone, no network, picks up uncommitted annotation
# edits) via the standard CMake override variable
# FETCHCONTENT_SOURCE_DIR_YGM.
module load craype-x86-trento libfabric/match_SHS craype-network-ofi perftools-base/25.09.0 craype/2.7.35
module swap PrgEnv-cray PrgEnv-gnu 2>&1 || module load PrgEnv-gnu/8.7.0
module load gcc-native/11.2
module load flux_wrappers/0.1 xpmem/2.6.5 cray-libsci/25.09.0 cray-mpich/9.0.1 python/3.13.2

export CC=/opt/cray/pe/mpich/9.0.1/ofi/gnu/11.2/bin/mpicc
export CXX=/opt/cray/pe/mpich/9.0.1/ofi/gnu/11.2/bin/mpicxx
export LD_LIBRARY_PATH=/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib/default64:/usr/lib64:$LD_LIBRARY_PATH

echo "CC=$CC"; $CC --version | head -1
echo "CXX=$CXX"; $CXX --version | head -1

WS=${WS}
DFTRACER_ROOT=$WS/venv-src-gnu/lib/python3.13/site-packages/dftracer
YGM_SRC=$WS/annotated/source
BENCH_SRC=$WS/ygm-bench/source
BENCH_BUILD=$WS/ygm-bench/build

cmake -S "$BENCH_SRC" -B "$BENCH_BUILD" \
  -DCMAKE_INSTALL_PREFIX="$WS/ygm-bench/install" \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo \
  -DFETCHCONTENT_SOURCE_DIR_YGM="$YGM_SRC" \
  -DDFTRACER_ROOT="$DFTRACER_ROOT" \
  -DCMAKE_CXX_STANDARD=20 \
  -DCMAKE_CXX_STANDARD_REQUIRED=ON \
  -DCMAKE_CXX_COMPILER="$CXX" \
  -DCMAKE_C_COMPILER="$CC" \
  -DYGM_BUILD_TESTS=OFF

cmake --build "$BENCH_BUILD" -j4
