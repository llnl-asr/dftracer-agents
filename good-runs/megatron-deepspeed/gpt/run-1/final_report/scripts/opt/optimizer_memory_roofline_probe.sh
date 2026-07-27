#!/bin/bash
# STEP 9d (memory) -- GATING MEASUREMENT, NOT EXECUTED THIS PASS.
# Purpose: classify the GPT-350M train_step as memory-bandwidth-bound vs compute-bound
# before any memory-layer lever is applied (Williams et al., Roofline, CACM 2009,
# https://doi.org/10.1145/1498765.1498785).
#
# Prereq: an allocation id supplied by the user. This script NEVER requests one.
# Usage:  flux proxy <alloc-id> bash scripts/optimizer_memory_roofline_probe.sh
set -euo pipefail
WS="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$WS/scripts/env.sh"
source "$WS/install/venv/bin/activate"

# 1) Node-level memory counters: the baseline run produced NO service_*.pfw files,
#    so peak/available node memory was unknown. Bracket the launch with the daemon.
#    (session_service_start / session_service_stop via MCP, one instance per node, -c1.)

# 2) Per-kernel HBM traffic + achieved occupancy for the steady-state iterations only.
#    Iter 1 includes ~68s of apex JIT and MUST be excluded.
export ROCPROF_OUT="$WS/artifacts/09d_memory_roofline"
mkdir -p "$ROCPROF_OUT"
# rocprofv3 counter set: HBM read/write bytes + valu/mfma busy -> arithmetic intensity
#   rocprofv3 --kernel-trace --stats -d "$ROCPROF_OUT" -- <the baseline launch cmd>
# Then: AI = FLOPs / HBM_bytes ; compare against MI300A's HBM3 peak BW and peak FLOPs.
echo "NOT EXECUTED -- proposal-only pass. Fill in the baseline launch command from"
echo "scripts/baseline_trace.sh and run under a user-named allocation."
