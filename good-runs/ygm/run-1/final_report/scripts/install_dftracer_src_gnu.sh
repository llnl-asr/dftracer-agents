#!/bin/bash
# Rebuild dftracer CORE from czgitlab source using the GNU MPICH wrapper
# (matching YGM's own toolchain, forced onto GNU by the Cray-clang20/spdlog-fmt
# consteval bug) instead of crayclang -- avoids linking two different MPI
# runtimes (libmpi_cray + libmpi_gnu_112) into the same traced process, which
# is a documented double-free-at-exit crash risk (system-tuolumne RULE 0 table).
set -e
set -o pipefail

WS=${WS}
module load craype-x86-trento libfabric/match_SHS craype-network-ofi perftools-base/25.09.0 craype/2.7.35
module swap PrgEnv-cray PrgEnv-gnu 2>&1 || module load PrgEnv-gnu/8.7.0
module load gcc-native/11.2
module load flux_wrappers/0.1 xpmem/2.6.5 cray-libsci/25.09.0 cray-mpich/9.0.1 python/3.13.2

VENV=$WS/venv-src-gnu
rm -rf $VENV
python -m venv $VENV
source $VENV/bin/activate
export VIRTUAL_ENV=$VENV
export PATH=$VENV/bin:$PATH

export MPICC=/opt/cray/pe/mpich/9.0.1/ofi/gnu/11.2/bin/mpicc
export MPICXX=/opt/cray/pe/mpich/9.0.1/ofi/gnu/11.2/bin/mpicxx
export CC=$MPICC
export CXX=$MPICXX

export DFTRACER_BUILD_TYPE=RelWithDebInfo
export DFTRACER_ENABLE_TESTS=OFF
export DFTRACER_ENABLE_DLIO_BENCHMARK_TESTS=OFF
export DFTRACER_ENABLE_PAPER_TESTS=OFF
export DFTRACER_ENABLE_MPI=ON

export DFTRACER_ENABLE_HDF5=ON
export HDF5_ROOT=/usr
export HDF5_DIR=/usr
export HDF5_PREFER_PARALLEL=ON

# Same MPI impl/version as the crayclang build (still Cray MPICH 9.0.1, just
# a different compiler front-end/wrapper) -- DFTRACER_MPI_IMPL/BRAHMA_MPI_VERSION
# do NOT change with the compiler switch.
export DFTRACER_CMAKE_ARGS="-DPython3_EXECUTABLE=$VENV/bin/python -DPython3_ROOT_DIR=$VENV -DDFTRACER_MPI_IMPL=CRAYMPICH -DBRAHMA_MPI_VERSION=900001 -DHDF5_C_COMPILER_EXECUTABLE=/usr/bin/h5cc -DHDF5_PREFER_PARALLEL=ON -DHDF5_ROOT=/usr -DCMAKE_PREFIX_PATH=/usr -DHDF5_NO_FIND_PACKAGE_CONFIG_FILE=ON"

export CMAKE_POLICY_VERSION_MINIMUM=3.5
export LDFLAGS=-ldl
export LD_LIBRARY_PATH=/usr/lib64:/opt/cray/pe/lib64:/opt/cray/lib64:$LD_LIBRARY_PATH
export JOBS=8
export CMAKE_BUILD_PARALLEL_LEVEL=8

pip install --upgrade pip setuptools wheel setuptools_scm 2>&1 | tail -5

echo "=== Installing dftracer (core) from czgitlab develop, GNU MPICH wrapper ==="
pip install -v --no-cache-dir --upgrade \
  "git+ssh://git@czgitlab.llnl.gov:7999/dftracer/dftracer.git@develop" \
  2>&1 | tee $WS/artifacts/dftracer_src_install_gnu.log

echo "=== DONE core=$? ==="
