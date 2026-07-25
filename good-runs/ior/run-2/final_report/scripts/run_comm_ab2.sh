#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

set -u
WS=${WS}
source $WS/scripts/env.sh
export LD_LIBRARY_PATH=$WS/install_hdf5/lib:$WS/install_dftracer/lib/python3.13/site-packages/dftracer/lib64:$LD_LIBRARY_PATH
IOR=$WS/install_ann/bin/ior
LOG=$WS/artifacts/05c_comm_ab2.log
: > $LOG
run_one() {
  V=$1; rep=$2
  OUT=$WS/dataset/comm2_${V}_rep${rep}
  mkdir -p $OUT
  unset MPICH_SHARED_MEM_COLL_OPT
  if [ "$V" = "smem" ]; then export MPICH_SHARED_MEM_COLL_OPT=1; fi
  echo "===== VARIANT=$V REP=$rep SMEM=${MPICH_SHARED_MEM_COLL_OPT:-default} $(date +%T) =====" >> $LOG
  flux run -N 8 -n 512 --exclusive $IOR -a HDF5 -b 16m -t 4k -s 32 -C -F -o $OUT/testFile >> $LOG 2>&1
  rm -rf $OUT
}
run_one smem 1; run_one ctrl 1
run_one ctrl 2; run_one smem 2
run_one smem 3; run_one ctrl 3
echo ALL_DONE >> $LOG
