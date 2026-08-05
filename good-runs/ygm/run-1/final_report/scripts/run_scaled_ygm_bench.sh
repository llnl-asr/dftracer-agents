#!/bin/bash
# Reproduces the session's final validated scaled run:
#   ygm-bench/build/src/around_the_world_ygm -n 5600 -t 1 -p
#   4 nodes x 32 ranks/node = 128 ranks total, GNU toolchain, single MPI runtime
#   full annotation (9 hot-loop functions in comm.ipp) + DFTRACER SELECTIVE
#   aggregation (see software-ygm skill: hot-loop over-annotation collapsed
#   traced perf at scale; selective aggregation keeps dft_cnt visibility at
#   <1MB/rank instead of >1GB/rank unaggregated).
#
# NOTE: this exact final run was launched interactively via ad-hoc
# `flux submit`/`flux jobs` (raw FLUX_URI=ssh://... connection, NOT
# `flux proxy`, which was hanging/unreliable this session) with no saved
# script. This script reconstructs it using `flux run` (blocking) for a
# straightforward, reproducible validation invocation -- functionally
# equivalent to the interactive session's submit+poll sequence.
#
# Usage: ./run_scaled_ygm_bench.sh <flux-alloc-id-or-blank-if-inside-alloc>
set -e

# --- module/toolchain setup, identical to scripts/build_gnu_src_final.sh ---
module load craype-x86-trento libfabric/match_SHS craype-network-ofi perftools-base/25.09.0 craype/2.7.35
module swap PrgEnv-cray PrgEnv-gnu 2>&1 || module load PrgEnv-gnu/8.7.0
module load gcc-native/11.2
module load flux_wrappers/0.1 xpmem/2.6.5 cray-libsci/25.09.0 cray-mpich/9.0.1 python/3.13.2

export CC=/opt/cray/pe/mpich/9.0.1/ofi/gnu/11.2/bin/mpicc
export CXX=/opt/cray/pe/mpich/9.0.1/ofi/gnu/11.2/bin/mpicxx

WS="${WS:-${WS}}"
DFTRACER_ROOT="$WS/venv-src-gnu/lib/python3.13/site-packages/dftracer"

export LD_LIBRARY_PATH=/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib/default64:/usr/lib64:/opt/cray/pe/lib64:/opt/cray/lib64:$DFTRACER_ROOT/lib64:$LD_LIBRARY_PATH

BINARY="$WS/ygm-bench/build/src/around_the_world_ygm"
TRACE_DIR="$WS/ygm_bench_scale/traces_repro_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$TRACE_DIR"

# --- dftracer FUNCTION-mode + SELECTIVE aggregation env ---
export DFTRACER_INIT=FUNCTION
export DFTRACER_ENABLE=1
export DFTRACER_DATA_DIR=all
export DFTRACER_INC_METADATA=1
export DFTRACER_LOG_FILE="$TRACE_DIR/atw"
export DFTRACER_ENABLE_AGGREGATION=1
export DFTRACER_AGGREGATION_TYPE=SELECTIVE
export DFTRACER_AGGREGATION_FILE="$WS/tmp/dftracer_aggregation_rules.yaml"
export DFTRACER_TRACE_INTERVAL_MS=5000

# --- YGM comm buffer settings confirmed stable at this rank density ---
# (default YGM_COMM_NUM_IRECVS=8 x 1GB/irecv = 8GB/rank x 32 ranks/node =
#  256GB/node, well under Tuolumne's ~502GB/node -- no OOM at 32 ranks/node)

echo "Binary:    $BINARY"
echo "Trace dir: $TRACE_DIR"
echo "Launching: flux run -N4 -n128 $BINARY -n 5600 -t 1 -p"

flux run -N4 -n128 "$BINARY" -n 5600 -t 1 -p

echo "Done. Traces written under $TRACE_DIR (~55MB / 128 rank files expected)."
echo "Reference result (this session, 2026-08-04): 660.04s (11.0 min), 716,800 total hops."
