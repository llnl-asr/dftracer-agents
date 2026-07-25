#!/bin/bash
# I/O optimization variant runner (UNTRACED bandwidth A/B, 512 ranks / 8 nodes).
# Reproduces artifacts/05a_io_v*.log from the original session.
# Usage: run_io_variant.sh <variant_name> <nreps>
#   variant_name in {v0_baseline, v1_collmeta, v2_romio_cb, v3_align1m}
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

set -u
VAR="${1:?variant name}"
NREPS="${2:-5}"
OUTPUT_ROOT="${OUTPUT_ROOT:-$WS/final_folder_validate}"

export LD_LIBRARY_PATH="$WS/install_hdf5/lib:$WS/install_dftracer/lib/python3.13/site-packages/dftracer/lib64:${LD_LIBRARY_PATH:-}"
export DFTRACER_ENABLE=0
IOR_EXTRA=""
unset MPICH_MPIIO_HINTS
unset CRAY_CB_NODES_MULTIPLIER
case "$VAR" in
  v0_baseline) ;;
  v1_collmeta) IOR_EXTRA="--hdf5.collectiveMetadata" ;;
  v2_romio_cb) export MPICH_MPIIO_HINTS="*:romio_cb_write=enable:romio_cb_read=enable:cb_buffer_size=16777216"; export CRAY_CB_NODES_MULTIPLIER=2 ;;
  v3_align1m) IOR_EXTRA="--hdf5.setAlignment=1m" ;;
  *) echo "unknown variant $VAR"; exit 2 ;;
esac
OUT="$OUTPUT_ROOT/opt_io/$VAR"
mkdir -p "$OUT"

for r in $(seq 1 "$NREPS"); do
  echo "=== REP $r ==="
  flux run -N 8 -n 512 "$WS/install_ann/bin/ior" -a HDF5 -b 16m -t 4k -s 32 -C -F $IOR_EXTRA -o "$OUT/testFile"
  echo "=== REP $r exit=$? ==="
done
rm -rf "$OUT"
echo "DONE $VAR"
