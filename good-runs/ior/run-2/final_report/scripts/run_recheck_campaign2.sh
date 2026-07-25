#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

# I/O optimization variant runner (UNTRACED bandwidth A/B, 512 ranks / 8 nodes)
# Usage: run_io_variant.sh <variant_name> <nreps>
# Every variant keeps the SAME IOR access pattern (-a HDF5 -b 16m -t 4k -s 32 -C -F).
# NOTE: flux run propagates the caller environment by default; do NOT use -x
# (in flux, -x means --exclusive and will swallow the following token as argv).
set -u
WS="${WS}"
NREPS="${1:-3}"
source "$WS/scripts/env.sh"
set +e
export LD_LIBRARY_PATH="$WS/install_hdf5/lib:$WS/install_dftracer/lib/python3.13/site-packages/dftracer/lib64:$LD_LIBRARY_PATH"
export DFTRACER_ENABLE=0
IOR="$WS/install_ann/bin/ior"
LOG="$WS/artifacts/05a_recheck_v0v3_extra.log"
: > "$LOG"
STAMP=$(date +%s)
cd "$WS"

run_arm () {
  ARM="$1"
  REP="$2"
  unset MPICH_SHARED_MEM_COLL_OPT
  unset MPICH_OFI_NIC_POLICY
  unset MPICH_MPIIO_HINTS
  unset CRAY_CB_NODES_MULTIPLIER
  EXTRA=""
  case "$ARM" in
    v0)        ;;
    v3align1m) EXTRA="--hdf5.setAlignment=1m" ;;
    shmcoll)   export MPICH_SHARED_MEM_COLL_OPT=1 ;;
    nicnuma)   export MPICH_OFI_NIC_POLICY=NUMA ;;
  esac
  OUT="$WS/dataset/recheck_${STAMP}_${ARM}_rep${REP}"
  rm -rf "$OUT"
  mkdir -p "$OUT"
  echo "===== ARM=$ARM REP=$REP extra=[$EXTRA] shm=[${MPICH_SHARED_MEM_COLL_OPT:-unset}] nic=[${MPICH_OFI_NIC_POLICY:-unset}] out=$OUT $(date +%T) =====" >> "$LOG"
  flux run -N 8 -n 512 --exclusive "$IOR" -a HDF5 -b 16m -t 4k -s 32 -C -F $EXTRA -o "$OUT/testFile" >> "$LOG" 2>&1
  echo "exit=$?" >> "$LOG"
  echo "DU_BYTES=$(du -sb "$OUT" 2>/dev/null | cut -f1) NFILES=$(ls "$OUT" | wc -l)" >> "$LOG"
  rm -rf "$OUT"
}

for rep in $(seq 1 "$NREPS"); do
  for arm in v0 v3align1m; do
    run_arm "$arm" "$rep"
  done
done
echo ALL_DONE >> "$LOG"
