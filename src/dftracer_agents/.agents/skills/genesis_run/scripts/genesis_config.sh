#!/bin/bash
# Shared definitions for a genesis sweep. Sourced by the runner, driver,
# compaction and README steps so they cannot disagree about paths or dimensions.
#
# Everything is driven by the per-system folder, so nothing here is machine
# specific: consult the system skill for this machine's values and set them via
# the environment or a per-system override file.

: "${GENESIS_WS:?set GENESIS_WS to the session workspace}"
SYSTEM="${GENESIS_SYSTEM:-$(hostname | sed 's/[0-9]*$//')}"

# The isolated per-system folder: env, venv, build, dataset symlink, traces.
G="${GENESIS_G:-$GENESIS_WS/genesis/$SYSTEM}"

# The corpus lives INSIDE the system folder and repeats the system name, so the
# directory is self-describing if copied or archived on its own.
GENESIS="${GENESIS_ROOT:-$G/traces/$SYSTEM}"
STATE="${GENESIS_STATE:-$G/artifacts/state}"
LOGDIR="${GENESIS_LOGDIR:-$G/artifacts}"

BINARY="${GENESIS_BINARY:?set GENESIS_BINARY to the executable built for THIS system}"
# Per-system venv: everything python/dftracer comes from $G, never the session.
PY="${GENESIS_PY:-$G/venv/bin/python}"
DFT_PKG="${GENESIS_DFT_PKG:-$(ls -d "$G"/venv/lib/python3*/site-packages/dftracer 2>/dev/null | head -1)}"
DFT_BIN="${GENESIS_DFT_BIN:-$DFT_PKG/bin/dftracer_service}"
SCRIPTS="${GENESIS_SCRIPTS:-$G/scripts}"

# Scheduler: flux or slurm. Auto-detected from the enclosing allocation.
if [ -z "${GENESIS_SCHED:-}" ]; then
  # `command -v flux` is not enough: some Slurm sites ship a flux binary with
  # no running instance. Flux only if an instance actually answers.
  if [ -n "${SLURM_JOB_ID:-}" ] || ! flux getattr size >/dev/null 2>&1; then
    GENESIS_SCHED=slurm
  else
    GENESIS_SCHED=flux
  fi
fi
# GPU tracing backend compiled into this system's dftracer: hip | cuda | none.
GENESIS_GPU="${GENESIS_GPU:-hip}"
# Node power source: variorum (service-side) | nvml (nvidia-smi sampler) | none.
GENESIS_POWER="${GENESIS_POWER:-variorum}"
GENESIS_AGG_FILE="${GENESIS_AGG_FILE:-$G/aggregation.yaml}"

# --- Dimensions -------------------------------------------------------------
# Defaults are placeholders; ALWAYS set these from the plan, and derive the ppn
# ladder from this machine's GPUs/cores per node (see the system skill).
# Inputs: either GENESIS_INPUTS (space-separated case names whose args the
# runner builds via GENESIS_ARGS_TEMPLATE, '{case}' substituted), or
# GENESIS_INPUTS_FILE with one "case<TAB>app args" line per input -- the general
# form, since most apps' args are not a single number.
declare -A CASE_ARGS=()
if [ -n "${GENESIS_INPUTS_FILE:-}" ] && [ -f "$GENESIS_INPUTS_FILE" ]; then
  INPUTS=()
  while IFS=$'\t' read -r _c _a; do
    [ -z "$_c" ] && continue; case "$_c" in \#*) continue ;; esac
    INPUTS+=("$_c"); CASE_ARGS[$_c]="$_a"
  done < "$GENESIS_INPUTS_FILE"
fi
case_args() {  # case -> the application's argument string
  if [ -n "${CASE_ARGS[$1]+x}" ]; then echo "${CASE_ARGS[$1]}"
  else
    local t="${GENESIS_ARGS_TEMPLATE:-}"
    [ -z "$t" ] && t='nx={case} ny={case} nz={case}'   # miniFE-style default
    echo "${t//'{case}'/$1}"
  fi
}
[ -n "${GENESIS_INPUTS:-}" ]      && read -r -a INPUTS      <<< "$GENESIS_INPUTS"
[ -n "${GENESIS_NODE_SCALES:-}" ] && read -r -a NODE_SCALES <<< "$GENESIS_NODE_SCALES"
[ -n "${GENESIS_PPNS:-}" ]        && read -r -a PPNS        <<< "$GENESIS_PPNS"
: "${INPUTS:?set GENESIS_INPUTS}" ; : "${NODE_SCALES:?set GENESIS_NODE_SCALES}" ; : "${PPNS:?set GENESIS_PPNS}"

CORES_PER_RANK="${GENESIS_CORES_PER_RANK:-8}"
GPUS_PER_RANK="${GENESIS_GPUS_PER_RANK:-1}"

# --- PAPI partition ---------------------------------------------------------
# MUST be derived and probe-verified on THIS machine (skill STEP 6) and written
# to $G/papi_sets.txt as "name:COUNTER,COUNTER,..." lines. Never copied.
PAPI_SETS=()
if [ -f "$G/papi_sets.txt" ]; then
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    case "$line" in \#*) continue ;; esac
    PAPI_SETS+=("$line")
  done < "$G/papi_sets.txt"
fi
if [ "${#PAPI_SETS[@]}" -eq 0 ]; then
  echo "FATAL: no PAPI sets in $G/papi_sets.txt (derive and probe-verify them on THIS machine -- skill STEP 6)"
  exit 1
fi

if [ -n "${GENESIS_SETS:-}" ]; then
  _keep=(); for _e in "${PAPI_SETS[@]}"; do
    [[ ",$GENESIS_SETS," == *",${_e%%:*},"* ]] && _keep+=("$_e")
  done; PAPI_SETS=("${_keep[@]}")
fi

leaf_dir() {  # case nodes ppn
  echo "$GENESIS/$1/nodes_${2}/ppn_${3}"
}
run_key()  {  # case nodes ppn set
  echo "${1}_N${2}_ppn${3}_${4}"
}
leaf_key() {  # case nodes ppn -- one leaf = all PAPI sets of one cell
  echo "${1}_N${2}_ppn${3}"
}
