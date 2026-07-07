#!/bin/bash
export LD_LIBRARY_PATH="/opt/cray/pe/lib64:/opt/cray/lib64:/opt/cray/pe/papi/7.2.0.2/lib64:/opt/cray/pe/pmi/6.0.15/lib:/opt/cray/pe/mpich/9.0.1/ofi/crayclang/20.0/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce-clang/x86_64/lib:/usr/lib64:/usr/WS2/haridev/dftracer-agents/workspaces/montage/20260706_062459/venv/lib/python3.13/site-packages/dftracer/lib64"
export DFTRACER_ENABLE=1
export DFTRACER_INIT=FUNCTION
export DFTRACER_INC_METADATA=1
export DFTRACER_DATA_DIR=all
export DFTRACER_LOG_FILE=/p/lustre5/haridev/dftracer-pegasus-montage/traces_2mass_true_lustre/montage-2mass-truelustre
cd /p/lustre5/haridev/dftracer-pegasus-montage/workflow_run/work_2mass_lustre/haridev/pegasus/montage/run0001
rm -f montage-0.dag.rescue
exec /usr/WS2/haridev/dftracer-agents/workspaces/pegasus_montage/pegasus/bin/pegasus-mpi-cluster -s -v montage-0.dag
