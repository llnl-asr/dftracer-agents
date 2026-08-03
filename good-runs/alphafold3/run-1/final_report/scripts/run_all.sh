#!/bin/bash
# Run every case in this session's evidence chain, in order.
# Pass the active Flux allocation id as $1 (see `flux jobs`).
#
# NOTE: a bare `flux run` queues a NEW job instead of using your
# allocation -- always go through `flux proxy <alloc>`.
set -e
ALLOC="${1:-}"
if [ -z "$ALLOC" ]; then echo "usage: $0 <flux_alloc_id>"; exit 1; fi
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

echo "### 1/8 baseline 1eby ###"
bash "$HERE/run_baseline_1eby.sh" baseline_1eby
echo "### 2/8 baseline 1eby replicate ###"
bash "$HERE/run_baseline_1eby.sh" baseline_1eby_r2
echo "### 3/8 baseline 1e66 ###"
bash "$HERE/run_baseline_1e66.sh" baseline_1e66
echo "### 4/8 baseline 1e66 replicate ###"
bash "$HERE/run_baseline_1e66.sh" baseline_1e66_r2
echo "### 5/8 compute optimizer variants (1eby) ###"
for v in control triton autotune tightbucket jitcache; do
  bash "$HERE/run_compute_opt1.sh" "$v" || echo "  (variant $v failed/no-op -- expected for jitcache without the command-buffer workaround, see REPORT.md Section 7 row 6)"
done
echo "### 6/8 memory optimizer A/B (1eby, 3 reps/side) ###"
for tag in a b c; do
  bash "$HERE/run_opt_memory_noprealloc.sh" "$tag" ctrl
  bash "$HERE/run_opt_memory_noprealloc.sh" "$tag" noprealloc
done
echo "### 7/8 DECISIVE verification: bucket alignment at 1e66 (validated on a real GPU allocation, see REPORT.md) ###"
bash "$HERE/run_verify_1e66_bucket1088.sh"
echo "### 8/8 verification: memory win transfer check at 1e66 ###"
bash "$HERE/run_verify_1e66_noprealloc.sh"

echo "run_all.sh complete. Compare wall times against REPORT.md Sections 2, 7, 7b."
