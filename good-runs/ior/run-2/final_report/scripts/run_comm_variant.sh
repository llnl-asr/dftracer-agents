#!/bin/bash
# Communication optimization variant runner (untraced A/B), 512 ranks / 8 nodes.
# Reproduces artifacts/05c_comm_ab.log (NUMA/NIC affinity) and
# artifacts/05c_comm_ab2.log (shared-memory MPI collectives) from the
# original session, merged into one parameterized script.
# Usage: run_comm_variant.sh <ctrl|numa|shmcoll> <nreps>
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

set -u
VAR="${1:?variant name (ctrl|numa|shmcoll)}"
NREPS="${2:-3}"
OUTPUT_ROOT="${OUTPUT_ROOT:-$WS/final_folder_validate}"

export LD_LIBRARY_PATH="$WS/install_hdf5/lib:$WS/install_dftracer/lib/python3.13/site-packages/dftracer/lib64:${LD_LIBRARY_PATH:-}"
export DFTRACER_ENABLE=0
IOR="$WS/install_ann/bin/ior"

unset MPICH_OFI_NIC_POLICY
unset MPICH_SHARED_MEM_COLL_OPT
case "$VAR" in
  ctrl) ;;
  numa) export MPICH_OFI_NIC_POLICY=NUMA ;;
  shmcoll) export MPICH_SHARED_MEM_COLL_OPT=1 ;;
  *) echo "unknown variant $VAR"; exit 2 ;;
esac

for rep in $(seq 1 "$NREPS"); do
  OUT="$OUTPUT_ROOT/comm_${VAR}_rep${rep}"
  mkdir -p "$OUT"
  echo "===== VARIANT=$VAR REP=$rep ====="
  flux run -N 8 -n 512 --exclusive "$IOR" -a HDF5 -b 16m -t 4k -s 32 -C -F -o "$OUT/testFile"
  echo "exit=$?"
  rm -rf "$OUT"
done
echo ALL_DONE
