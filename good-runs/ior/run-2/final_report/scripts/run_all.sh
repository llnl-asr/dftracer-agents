#!/bin/bash
# Run every case in the optimization ladder, in order.
# Pass the active Flux allocation id as $1 (see `flux jobs`).
#
# NOTE: a bare `flux run` queues a NEW job instead of using your
# allocation -- always go through `flux proxy <alloc>`.
set -e
ALLOC="${1:-&lt;flux-jobid&gt;}"
if [ -z "$ALLOC" ]; then echo "usage: $0 <flux_alloc_id>"; exit 1; fi
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"
OBJ="$WS/annotated/source/object"

echo "=== baseline ==="
flux proxy "$ALLOC" bash "$HERE/run_baseline.sh"

echo "=== do_bootstrap ==="
flux proxy "$ALLOC" bash "$HERE/run_do_bootstrap.sh"

echo "=== do_configure ==="
flux proxy "$ALLOC" bash "$HERE/run_do_configure.sh"

echo "=== do_configure2 ==="
flux proxy "$ALLOC" bash "$HERE/run_do_configure2.sh"

echo "=== do_configure3 ==="
flux proxy "$ALLOC" bash "$HERE/run_do_configure3.sh"

echo "=== dftracer_run_baseline_rep1 ==="
flux proxy "$ALLOC" bash "$HERE/run_dftracer_run_baseline_rep1.sh"

echo "=== analyze_wrapper ==="
flux proxy "$ALLOC" bash "$HERE/run_analyze_wrapper.sh"

echo "=== comm_ab ==="
flux proxy "$ALLOC" bash "$HERE/run_comm_ab.sh"

echo "=== comm_ab2 ==="
flux proxy "$ALLOC" bash "$HERE/run_comm_ab2.sh"

echo "=== run_with_dftracer_opt_align1m ==="
flux proxy "$ALLOC" bash "$HERE/run_run_with_dftracer_opt_align1m.sh"

echo "=== recheck_campaign ==="
flux proxy "$ALLOC" bash "$HERE/run_recheck_campaign.sh"

echo "=== recheck_campaign2 ==="
flux proxy "$ALLOC" bash "$HERE/run_recheck_campaign2.sh"

echo "=== render REPORT.pdf / README.pdf ==="
bash "$HERE/render_pdf.sh" || echo "WARN: PDF render skipped/failed (non-fatal to run_all.sh)"
