#!/bin/bash
# Run every case in the optimization ladder, in order.
# Pass the active Flux allocation id as $1 (see `flux jobs`).
#
# NOTE: a bare `flux run` queues a NEW job instead of using your
# allocation -- always go through `flux proxy <alloc>`.
set -e
ALLOC="${1:-}"
if [ -z "$ALLOC" ]; then echo "usage: $0 <flux_alloc_id>"; exit 1; fi
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"
OBJ="$WS/annotated/source/object"

echo "=== baseline ==="
flux proxy "$ALLOC" bash "$HERE/run_baseline.sh"

echo "=== check_env ==="
flux proxy "$ALLOC" bash "$HERE/run_check_env.sh"

echo "=== check_env2 ==="
flux proxy "$ALLOC" bash "$HERE/run_check_env2.sh"

echo "=== build_apex ==="
flux proxy "$ALLOC" bash "$HERE/run_build_apex.sh"

echo "=== verify_apex ==="
flux proxy "$ALLOC" bash "$HERE/run_verify_apex.sh"

echo "=== check_torch ==="
flux proxy "$ALLOC" bash "$HERE/run_check_torch.sh"

echo "=== try_torchvision ==="
flux proxy "$ALLOC" bash "$HERE/run_try_torchvision.sh"

echo "=== verify_tv ==="
flux proxy "$ALLOC" bash "$HERE/run_verify_tv.sh"

echo "=== uninstall_tv ==="
flux proxy "$ALLOC" bash "$HERE/run_uninstall_tv.sh"

echo "=== run_slice ==="
flux proxy "$ALLOC" bash "$HERE/run_run_slice.sh"

echo "=== run_baseline_training ==="
flux proxy "$ALLOC" bash "$HERE/run_run_baseline_training.sh"

echo "=== run_diagnostic_8rank ==="
flux proxy "$ALLOC" bash "$HERE/run_run_diagnostic_8rank.sh"

echo "=== run_diagnostic_4rank_nccl ==="
flux proxy "$ALLOC" bash "$HERE/run_run_diagnostic_4rank_nccl.sh"

echo "=== check_hostnames ==="
flux proxy "$ALLOC" bash "$HERE/run_check_hostnames.sh"

echo "=== run_2rank_connectivity_test ==="
flux proxy "$ALLOC" bash "$HERE/run_run_2rank_connectivity_test.sh"

echo "=== test_flux_mpi_env ==="
flux proxy "$ALLOC" bash "$HERE/run_test_flux_mpi_env.sh"

echo "=== run_ddp_pmi_direct ==="
flux proxy "$ALLOC" bash "$HERE/run_run_ddp_pmi_direct.sh"

echo "=== run_fixed_ddp_test ==="
flux proxy "$ALLOC" bash "$HERE/run_run_fixed_ddp_test.sh"

echo "=== run_ddp_with_ifname ==="
flux proxy "$ALLOC" bash "$HERE/run_run_ddp_with_ifname.sh"

echo "=== run_ddp_with_hsi_ip ==="
flux proxy "$ALLOC" bash "$HERE/run_run_ddp_with_hsi_ip.sh"

echo "=== run_ddp_hsn_ip_final ==="
flux proxy "$ALLOC" bash "$HERE/run_run_ddp_hsn_ip_final.sh"

echo "=== run_ddp_single_node ==="
flux proxy "$ALLOC" bash "$HERE/run_run_ddp_single_node.sh"

echo "=== run_ddp_torchrun ==="
flux proxy "$ALLOC" bash "$HERE/run_run_ddp_torchrun.sh"

echo "=== torchrun_wrapper ==="
flux proxy "$ALLOC" bash "$HERE/run_torchrun_wrapper.sh"

echo "=== install_torchrun_hpc ==="
flux proxy "$ALLOC" bash "$HERE/run_install_torchrun_hpc.sh"

echo "=== dryrun_wrapper ==="
flux proxy "$ALLOC" bash "$HERE/run_dryrun_wrapper.sh"

echo "=== allreduce_wrapper ==="
flux proxy "$ALLOC" bash "$HERE/run_allreduce_wrapper.sh"

echo "=== probe_wrapper ==="
flux proxy "$ALLOC" bash "$HERE/run_probe_wrapper.sh"

