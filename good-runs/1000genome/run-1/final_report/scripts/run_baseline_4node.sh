#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"
set -e
NODES=4; WORKERS=32
echo "Baseline 4-node PMC run (4000 VCF rows, 16 individuals jobs, 46 tasks)"

# Self-contained wrapper (inlined from the session's baseline_4node/scripts/
# pmc_wrapper.sh so this script does not depend on a non-anonymized file
# outside final_report/). KNOWN LIMITATION: the DAG itself
# (baseline_4node/1000-genome-pmc-run-4node/*.dag) is a Pegasus-planned
# artifact with per-task absolute paths baked in at plan time (staging dirs,
# pegasus-kickstart invocation, output locations under
# $WS/baseline_4node/1000-genome-pmc-run-4node/...). It cannot be relocated
# under OUTPUT_ROOT without re-running pegasus-plan, so task I/O still lands
# under $WS/baseline_4node -- only the PMC rescue/resume-state file and the
# dftracer trace output are isolated to OUTPUT_ROOT below. We pass
# -s/--skip-rescue so PMC does NOT silently no-op on an already-"done" DAG
# (confirmed bug: without -s, a reproduction run here reports tasks=46
# succeeded=0 in 0.001s instead of actually re-timing the workflow) -- this
# forces every task to actually re-execute so the reported wall time is real.
RESCUE="${OUTPUT_ROOT}/baseline_4node.rescue"
GEN_WRAPPER="${OUTPUT_ROOT}/baseline_4node_pmc_wrapper.sh"
mkdir -p "${OUTPUT_ROOT}/traces/raw"
cat > "$GEN_WRAPPER" <<INNER
#!/bin/bash
set -e
module load craype-x86-trento libfabric/match_SHS craype-network-ofi perftools-base/25.09.0 craype/2.7.35 PrgEnv-cray/8.7.0 flux_wrappers/0.1 xpmem/2.6.5 cce/20.0.0 cray-libsci/25.09.0 cray-mpich/9.0.1 python/3.13.2 2>/dev/null || true
source "${WS}/tools/venv/bin/activate"
export PYTHONPATH="${WS}/tools/pegasus/lib64/python3.6/site-packages"
export PATH="${WS}/tools/pegasus/bin:${WS}/tools/venv/bin:\$PATH"
export CONDOR_CONFIG="${WS}/tools/condor/etc/condor_config"
export LD_LIBRARY_PATH="/opt/cray/pe/lib64:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce-clang/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib/default64:/usr/lib64"
export DFTRACER_ENABLE=1 DFTRACER_INIT=FUNCTION DFTRACER_INC_METADATA=1 DFTRACER_DATA_DIR=all
export DFTRACER_LOG_FILE="${OUTPUT_ROOT}/traces/raw/baseline_4node_validate"
cd "${WS}/baseline_4node/1000-genome-pmc-run-4node"
DAG=\$(ls *.dag 2>/dev/null | head -1)
exec pegasus-mpi-cluster -v -s -r "${RESCUE}" "\$DAG"
INNER
chmod +x "$GEN_WRAPPER"
flux proxy "${FLUX_ALLOC:?set FLUX_ALLOC=<flux-alloc-id>}" flux run -N$NODES -n$WORKERS --env LD_LIBRARY_PATH="/opt/cray/pe/lib64:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce-clang/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib/default64:/usr/lib64" bash "$GEN_WRAPPER" 2>&1 | tee "${OUTPUT_ROOT}/baseline_4node_run.log"
echo "Expect wall time ~316.5s per REPORT.md Section 2"
