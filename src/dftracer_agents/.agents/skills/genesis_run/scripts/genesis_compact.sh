#!/bin/bash
# Compaction stage: for every (input, nodes, ppn) leaf, compact ALL of that
# cell's raw traces -- every rank, every node's service daemon, and all 8 PAPI
# counter-set runs -- into one compacted, chunked, gzipped trace set.
#
# The leaf therefore ends up holding both forms the request asked for:
#   raw/        per-PAPI-set directories, exactly as the runs produced them
#   compacted/  4 MB chunks over the whole cell, --verify'd against the input
#
# --verify makes dftracer_split compare event IDs in against event IDs out, so a
# lossy compaction is an error rather than a silent shortfall.
set -uo pipefail
WS=${GENESIS_WS:?set GENESIS_WS to the session workspace}
# THIS system's env and config -- never the session's (see genesis_run.sh).
GENESIS_G="${GENESIS_G:?set GENESIS_G to this genesis system folder}"
source "$GENESIS_G/env.sh"              > /dev/null 2>&1
source "$GENESIS_G/scripts/genesis_config.sh"
DL=$WS/venv/lib/python3.13/site-packages/dftracer
export LD_LIBRARY_PATH="$DL/lib64:$DL/lib:${LD_LIBRARY_PATH:-}"
SPLIT="$DL/bin/dftracer_split"
# LOGDIR/CSTATE come from genesis_config.sh: per-system, never shared.
mkdir -p "$LOGDIR"
CSTATE="$LOGDIR/compact_state"; touch "$CSTATE"

nleaf=0; nok=0; nfail=0
for nx in "${INPUTS[@]}"; do
 for N in "${NODE_SCALES[@]}"; do
  for P in "${PPNS[@]}"; do
    leaf=$(leaf_dir "$(case_slug "$nx")" "$N" "$P")
    ckey="nx${nx}_N${N}_ppn${P}"
    [ -d "$leaf/raw" ] || continue
    nleaf=$((nleaf+1))
    grep -q "^$ckey ok" "$CSTATE" 2>/dev/null && { nok=$((nok+1)); continue; }

    nraw=$(find "$leaf/raw" -name "*.pfw.gz" | wc -l)
    [ "$nraw" -eq 0 ] && continue
    rm -rf "$leaf/compacted"; mkdir -p "$leaf/compacted"

    # dftracer_split does NOT recurse: it globs *.pfw* in exactly the directory
    # it is given. This cell's traces live one level down in raw/papi_set*/, so
    # pointing it at raw/ finds zero files and exits 1. Stage a FLAT directory of
    # hardlinks instead (same filesystem, so no copy, no extra space).
    # App trace names already carry the set name and are unique; the per-node
    # service traces are all called service_<host>.pfw.gz and WOULD collide
    # across the 8 sets, so they get the set name spliced in.
    stage="$WS/tmp/_compact_stage_$ckey"
    rm -rf "$stage"; mkdir -p "$stage"
    for sdir in "$leaf"/raw/papi_*; do
      [ -d "$sdir" ] || continue
      sname=$(basename "$sdir"); sname=${sname#papi_}
      for f in "$sdir"/*.pfw.gz; do
        [ -e "$f" ] || continue
        b=$(basename "$f")
        case "$b" in
          service_*) ln "$f" "$stage/${b%.pfw.gz}__${sname}.pfw.gz" 2>/dev/null ;;
          *)         ln "$f" "$stage/$b" 2>/dev/null ;;
        esac
      done
    done
    nstage=$(find "$stage" -name "*.pfw.gz" | wc -l)
    if [ "$nstage" -ne "$nraw" ]; then
      echo "$ckey FAIL staged=$nstage != raw=$nraw" >> "$LOGDIR/compact_failures"
      nfail=$((nfail+1)); echo "FAIL $ckey staging mismatch $nstage/$nraw"; rm -rf "$stage"; continue
    fi

    "$SPLIT" -d "$stage" -o "$leaf/compacted" -n "$ckey" \
             -s 4 --compress --verify --executor-threads ${CTHREADS:-8} --io-threads ${CTHREADS:-8} \
             > "$LOGDIR/compact_${ckey}.log" 2>&1
    rc=$?
    rm -rf "$stage"
    nout=$(find "$leaf/compacted" -name "*.pfw.gz" | wc -l)
    vok=$(grep -c "Verification: PASSED" "$LOGDIR/compact_${ckey}.log" 2>/dev/null)
    # Assert the compacted set carries the SERVICE traces too, not only the app.
    "$WS/venv/bin/python" "$WS/scripts/genesis_check_compacted.py" \
        "$leaf/compacted" "$N" > "$leaf/compacted_check.json" 2>>"$LOGDIR/compact_${ckey}.log"
    cok=$?
    if [ "$rc" -eq 0 ] && [ "$nout" -gt 0 ] && [ "$vok" -ge 1 ] && [ "$cok" -eq 0 ]; then
      nev=$(grep -oE "[0-9]+ events" "$LOGDIR/compact_${ckey}.log" | tail -1)
      echo "$ckey ok raw=$nraw chunks=$nout $nev" >> "$CSTATE"
      nok=$((nok+1)); echo "OK   $ckey  raw=$nraw -> $nout chunks"
    else
      echo "$ckey FAIL rc=$rc raw=$nraw chunks=$nout verified=$vok content_ok=$cok :: $(cat "$leaf/compacted_check.json" 2>/dev/null | head -c 300)" >> "$LOGDIR/compact_failures"
      nfail=$((nfail+1)); echo "FAIL $ckey rc=$rc (see compact_${ckey}.log)"
    fi
  done
 done
done
echo "=== compaction: $nok ok, $nfail failed, $nleaf leaves seen ==="
