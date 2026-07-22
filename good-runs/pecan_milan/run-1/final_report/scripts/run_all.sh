#!/bin/bash
# Run every case in the optimization ladder, in order, and report epoch-2
# wall time for each so the -11.5% delta (baseline5 -> final_all_opt) can be
# checked for reproduction.
#
# Pass the active Flux allocation id as $1 (see flux jobs -a).
# NOTE: a bare flux run queues a NEW job instead of using your allocation --
# always go through flux proxy <alloc> first, or pass an alloc already
# proxied into this shell.
set -e
ALLOC="${1:?usage: $0 <flux_alloc_id>}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

echo "== Using Flux allocation: $ALLOC =="
echo "== Output root: $OUTPUT_ROOT =="

run_case() {
  local script="$1"
  local run="$2"
  echo ""
  echo "###### $run ######"
  flux proxy "$ALLOC" flux run -N4 -n16 -g1 -o mpibind=off bash "$HERE/$script" > "$OUTPUT_ROOT/$run.log" 2>&1
  cat "$OUTPUT_ROOT/$run.log"
  epoch2=$(grep -i "epoch 2" "$OUTPUT_ROOT/$run.log" | tail -1)
  echo "$run epoch-2 line: ${epoch2:-<not found, inspect $OUTPUT_ROOT/$run.log>}"
}

run_case run_baseline5.sh baseline5
run_case run_io_opt4_finalize.sh io_opt4_finalize
run_case run_opt5_losssync.sh opt5_losssync
run_case run_final_all_opt.sh final_all_opt

echo ""
echo "== Done. Compare baseline5 vs final_all_opt epoch-2 wall time above. =="
echo "== Reported: baseline5=31.54s, final_all_opt=27.90s (-11.5%). =="
echo "== torch.compile and Spindle FLUXOPT=high were evaluated and rejected --"
echo "== not run here; see ../REPORT.md and ../patches/ for that A/B evidence. =="
