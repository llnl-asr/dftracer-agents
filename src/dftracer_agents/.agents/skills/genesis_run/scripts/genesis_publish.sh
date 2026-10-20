#!/bin/bash
# Publish stage: MOVE each sealed leaf's raw/ (traces + run_*.json +
# validation_*.json) into its final archive location FIRST, then compact it IN
# PLACE there. Per leaf:
#   sealed -> copy raw -> verify bytes+count -> delete source raw
# then, over the archive:
#   compact in place -> any leaf without compacted/ gets COMPACTION_NOT_RUN.md
# raw/ is never deleted after compaction: the archive keeps raw + compacted.
set -uo pipefail
WS=${GENESIS_WS:?set GENESIS_WS to the session workspace}
GENESIS_G="${GENESIS_G:?set GENESIS_G to this genesis system folder}"
ARCHIVE="${GENESIS_ARCHIVE:?set GENESIS_ARCHIVE to <archive>/<app>/<system>}"
source "$GENESIS_G/scripts/genesis_config.sh"
mkdir -p "$ARCHIVE" "$LOGDIR"
PSTATE="$LOGDIR/publish_state"; touch "$PSTATE"

# Parallel copy with mpifileutils when present (flux or slurm), else cp -a.
# GENESIS_COPY=cp forces a serial copy (e.g. publishing from a login node with
# no allocation, where srun/flux run would have to queue).
copy_tree() {  # src dst_parent
  if [ "${GENESIS_COPY:-auto}" != cp ] && command -v dcp >/dev/null 2>&1; then
    if command -v flux >/dev/null 2>&1 && flux getattr size >/dev/null 2>&1; then
      flux run -N1 -n16 dcp -p "$1" "$2"
    else
      srun -N1 -n16 --overlap dcp -p "$1" "$2"
    fi
  else
    cp -a "$1" "$2"
  fi
}
# Cross-filesystem move: compare regular-file bytes and counts, never du.
fbytes() { find "$1" -type f -printf '%s\n' | awk '{s+=$1} END{print s+0}'; }
fcount() { find "$1" -type f | wc -l; }

nset=${#PAPI_SETS[@]}; npub=0
for nx in "${INPUTS[@]}"; do
 for N in "${NODE_SCALES[@]}"; do
  for P in "${PPNS[@]}"; do
    src=$(leaf_dir "$nx" "$N" "$P")
    lkey=$(leaf_key "$nx" "$N" "$P")
    grep -q "^$lkey published" "$PSTATE" && continue
    [ -d "$src/raw" ] || continue
    # SEALED = every PAPI set for this leaf validated ok in the checkpoint.
    # GENESIS_LEAF restricts this call to one leaf (the worker calls it the
    # moment that leaf seals).
    [ -n "${GENESIS_LEAF:-}" ] && [ "$GENESIS_LEAF" != "$lkey" ] && continue
    have=$(grep -E "^$(run_key "$nx" "$N" "$P" "")[^ ]+ ok" "$STATE" 2>/dev/null \
           | awk '{print $1}' | sort -u | wc -l)
    [ "$have" -ge "$nset" ] || { echo "skip $lkey: not sealed ($have/$nset)"; continue; }
    dst="$ARCHIVE/$nx/nodes_${N}/ppn_${P}"
    if [ -d "$dst/raw" ] && [ "$(fcount "$dst/raw")" -gt 0 ]; then
      echo "$lkey FAIL destination raw/ not empty: $dst" >> "$LOGDIR/publish_failures"; continue
    fi
    mkdir -p "$dst"
    copy_tree "$src/raw" "$dst/" >> "$LOGDIR/publish_${lkey}.log" 2>&1
    sb=$(fbytes "$src/raw"); db=$(fbytes "$dst/raw")
    sc=$(fcount "$src/raw"); dc=$(fcount "$dst/raw")
    if [ "$sb" != "$db" ] || [ "$sc" != "$dc" ]; then
      echo "$lkey FAIL copy mismatch bytes $sb/$db files $sc/$dc -- source kept" >> "$LOGDIR/publish_failures"
      continue
    fi
    rm -rf "$src/raw" "$src/compacted" "$src/compacted_check.json"
    ln -sfn "$dst" "$src/ARCHIVED"            # breadcrumb left in the workspace
    echo "$lkey published bytes=$db files=$dc dst=$dst" >> "$PSTATE"
    npub=$((npub+1)); echo "PUB  $lkey -> $dst"
  done
 done
done
echo "=== publish: $npub leaves moved ==="

# Compact IN PLACE in the archive. genesis_compact.sh writes COMPACTION_NOT_RUN.md
# into any leaf it fails on; if the compactor cannot start at all, the sweep
# below still marks every archived leaf that has no compacted output.
GENESIS_ROOT="$ARCHIVE" GENESIS_LOGDIR="$LOGDIR/archive_compact" \
  "$GENESIS_G/scripts/genesis_compact.sh"
crc=$?
find "$ARCHIVE" -mindepth 4 -maxdepth 4 -type d -name raw | while read -r raw; do
  leaf=$(dirname "$raw")
  if ! ls "$leaf"/compacted/*.pfw.gz >/dev/null 2>&1 && [ ! -f "$leaf/COMPACTION_NOT_RUN.md" ]; then
    printf '# Compaction did not run\n\ndate: %s\ncompactor exit: %s\n\n`raw/` is complete and authoritative. Re-run genesis_compact.sh with GENESIS_ROOT=%s.\n' \
      "$(date -Is)" "$crc" "$ARCHIVE" > "$leaf/COMPACTION_NOT_RUN.md"
    echo "NOTE $leaf: compaction did not run"
  fi
done
# Leaf contract (genesis_leaf_contract skill): every archived leaf carries a
# summary.json, then the deterministic checker gates the round.
APPNAME=${GENESIS_APP:-$(basename "$(dirname "$ARCHIVE")")}
find "$ARCHIVE" -mindepth 3 -maxdepth 3 -type d -path '*/nodes_*/ppn_*' | while read -r leaf; do
  "$PY" "$SCRIPTS/genesis_leaf_summary.py" "$leaf" "$APPNAME" "$SYSTEM" >> "$LOGDIR/publish_summary.log" 2>&1
done
CHECKER=${GENESIS_LEAF_CHECKER:-$SCRIPTS/genesis_validate_leaf.py}
if [ -f "$CHECKER" ]; then
  "$PY" "$CHECKER" "$ARCHIVE" --sets="$nset" > "$LOGDIR/leaf_contract.log" 2>&1
  echo "leaf contract: $(tail -1 "$LOGDIR/leaf_contract.log")"
fi
# chgperm every round: anything published is immediately group-usable.
[ -n "${GENESIS_CHGPERM:-}" ] && [ -x "$GENESIS_CHGPERM" ] && BACKEND=${GENESIS_CHGPERM_BACKEND:-local} QUIET=1 "$GENESIS_CHGPERM" "$ARCHIVE" >> "$LOGDIR/chgperm.log" 2>&1
exit 0
