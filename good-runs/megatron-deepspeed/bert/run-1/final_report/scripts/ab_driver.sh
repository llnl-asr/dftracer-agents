#!/bin/bash
WS="$WS"
ALLOC="$1"; shift
for spec in "$@"; do
  arm="${spec%%:*}"; port="${spec##*:}"
  envf="env.sh"; [ "$arm" = "B" ] && envf="env_baserocm.sh"
  name="ab_${arm}_${port}"
  s="$WS/scripts/run_${name}.sh"
  sed -e "s|ALLOC_ID=\"[^\"]*\"|ALLOC_ID=\"$ALLOC\"|" \
      -e "s|MASTER_PORT=[0-9]*|MASTER_PORT=$port|" \
      -e "s|RUN_NAME=.*|RUN_NAME=$name|" \
      -e "s|/scripts/env.sh|/scripts/$envf|" \
      -e "s|baseline_leakfix/traces/raw|$name/traces/raw|g" \
      "$WS/scripts/baseline_clean_4n16r.sh" > "$s"
  mkdir -p "$WS/$name/traces/raw"
  echo "=== $(date +%H:%M:%S) launching $name (env=$envf port=$port) ==="
  bash "$s" > "$WS/artifacts/${name}_launch.log" 2>&1
  echo "=== $(date +%H:%M:%S) $name done ==="
done
