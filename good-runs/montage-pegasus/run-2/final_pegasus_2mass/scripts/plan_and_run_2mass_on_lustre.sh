#!/bin/bash
# Reproduces the larger (102-image) 2MASS Pegasus/PMC run, genuinely
# executing on Lustre (not just staging metadata pointing there).
#
# CRITICAL GOTCHA: Pegasus namespaces the actual execution directory as
# <CWD-at-plan-time>/wf-scratch/<SITE>/<user>/pegasus/<workflow-name>/<run-id>
# regardless of the site catalog's sharedScratch path. If you `cd` into an
# NFS directory and run pegasus-plan there, jobs execute on NFS even with
# a Lustre-pointing sites.yml. To genuinely run on Lustre:
#   1. cd into a directory THAT IS ITSELF on Lustre before pegasus-plan
#   2. Different runs with the same workflow name ("montage") and run
#      number (run0001) SHARE the same wf-scratch dir if planned from the
#      same CWD -- move/clear it between independent runs to avoid
#      cross-run file contamination skewing trace-based measurements.
set -e

ALLOC_JOBID="$1"           # e.g. f3JSbA6awcdD
LUSTRE_WORKDIR="$2"        # e.g. /p/lustre5/$USER/dftracer-pegasus-montage/workflow_run
MONTAGE_ANN_BIN="$3"       # e.g. .../montage/<run>/install_ann/bin
PEGASUS_HOME="$4"          # e.g. .../pegasus_montage/pegasus
CONDOR_HOME="$5"           # e.g. .../pegasus_montage/condor
DFTRACER_LIB="$6"          # e.g. .../montage/<run>/venv/lib/python3.13/site-packages/dftracer/lib64
TRACES_DIR="$7"            # e.g. /p/lustre5/$USER/dftracer-pegasus-montage/traces_2mass
CENTER="${8:-56.7 24.0}"
DEGREES="${9:-1.0}"

mkdir -p "$LUSTRE_WORKDIR" "$TRACES_DIR"

# 1. Generate the DAX with PATH pointing at the ANNOTATED binaries so
#    every transformation in the workflow is dftracer-instrumented.
export PATH="$MONTAGE_ANN_BIN:$PEGASUS_HOME/bin:$CONDOR_HOME/bin:$CONDOR_HOME/sbin:$PATH"
export PYTHONPATH="$PEGASUS_HOME/lib64/python3.6/site-packages"
export CONDOR_CONFIG="$CONDOR_HOME/etc/condor_config"
export LD_LIBRARY_PATH="/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib/default64:/usr/lib64:$DFTRACER_LIB:${LD_LIBRARY_PATH}"

cd "$LUSTRE_WORKDIR"
rm -rf data
python3 ./montage-workflow.py --center "$CENTER" --degrees "$DEGREES" --band 2mass:j:blue

# 2. Plan (PMC-only code generator; no --cluster horizontal -- see
#    software-pegasus skill for why that combination corrupts the DAG).
rm -rf work_2mass
pegasus-plan \
  --conf ./pegasus.properties \
  --sites local \
  --dir work_2mass \
  --output-site local \
  data/montage-workflow.yml

RUN="$LUSTRE_WORKDIR/work_2mass/$(whoami)/pegasus/montage/run0001"

# 3. Verify the execution directory really is on Lustre before running --
#    this is the exact check that caught the NFS/Lustre mixup.
WORKDIR=$(grep -m1 "^TASK mProject" "$RUN/montage-0.dag" | grep -oP '(?<=-w )\S+')
echo "Execution working directory: $WORKDIR"
case "$WORKDIR" in
  /p/lustre*) echo "OK: running on Lustre" ;;
  *) echo "WARNING: NOT on Lustre! Re-check CWD at plan time." >&2 ;;
esac

# 4. Run via PMC under the Flux allocation.
WRAPPER=$(mktemp)
cat > "$WRAPPER" <<EOF
#!/bin/bash
export LD_LIBRARY_PATH="/opt/cray/pe/lib64:/opt/cray/lib64:/opt/cray/pe/papi/7.2.0.2/lib64:/opt/cray/pe/pmi/6.0.15/lib:/opt/cray/pe/mpich/9.0.1/ofi/crayclang/20.0/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce-clang/x86_64/lib:/usr/lib64:$DFTRACER_LIB"
export DFTRACER_ENABLE=1
export DFTRACER_INIT=FUNCTION
export DFTRACER_INC_METADATA=1
export DFTRACER_DATA_DIR=all
export DFTRACER_LOG_FILE=$TRACES_DIR/montage-2mass
cd $RUN
rm -f montage-0.dag.rescue
exec $PEGASUS_HOME/bin/pegasus-mpi-cluster -s -v montage-0.dag
EOF
chmod +x "$WRAPPER"

flux proxy "$ALLOC_JOBID" flux run -N1 -n32 bash "$WRAPPER"
