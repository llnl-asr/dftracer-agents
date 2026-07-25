#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

set -u
WS=${WS}
source $WS/scripts/env.sh
export LD_LIBRARY_PATH=$WS/install_hdf5/lib:$WS/install_dftracer/lib/python3.13/site-packages/dftracer/lib64:$LD_LIBRARY_PATH
IOR=$WS/install_ann/bin/ior
LOG=$WS/artifacts/05c_comm_ab.log
: > $LOG
for rep in 1 2 3; do
  for V in ctrl numa; do
    OUT=$WS/dataset/comm_${V}_rep${rep}
    mkdir -p $OUT
    unset MPICH_OFI_NIC_POLICY
    if [ "$V" = "numa" ]; then export MPICH_OFI_NIC_POLICY=NUMA; fi
    echo "===== VARIANT=$V REP=$rep NIC=${MPICH_OFI_NIC_POLICY:-default} $(date +%T) =====" >> $LOG
    flux run -N 8 -n 512 --exclusive $IOR -a HDF5 -b 16m -t 4k -s 32 -C -F -o $OUT/testFile >> $LOG 2>&1
    echo "exit=$?" >> $LOG
    rm -rf $OUT
  done
done
echo ALL_DONE >> $LOG
