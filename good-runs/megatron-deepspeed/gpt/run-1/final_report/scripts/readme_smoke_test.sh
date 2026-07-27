#!/bin/bash
# Static smoke test: does final_report/README.md actually contain everything
# needed to reproduce this session from this folder alone -- every script
# under scripts/ is named, config.ini/WORKSPACE_ROOT is explained, and there
# is at least one real, runnable command block. This does NOT execute the
# app (that is scripts/run_all.sh's job, and the "self-contained validation"
# step in session_final_report) -- it only checks the README's INSTRUCTIONS
# are complete enough for a reader who has never seen this session to follow
# them, so a thin or stale README fails fast instead of only being noticed
# by a human mid-reproduction.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FINAL="$HERE/.."
README="$FINAL/README.md"
FAIL=0

fail() { echo "MISSING: $1" >&2; FAIL=1; }

if [ ! -f "$README" ]; then
  echo "MISSING: README.md does not exist" >&2
  exit 1
fi

grep -qi "WORKSPACE_ROOT" "$README"  || fail "no mention of WORKSPACE_ROOT (reader won't know to set it)"
grep -qi "config.ini"     "$README"  || fail "no mention of config.ini (reader won't know it's the one place to set a real path)"

# Every script this session actually produced must be named somewhere in the
# README, or a reader has no way to know it exists / when to run it.
for s in "$HERE"/*.sh; do
  name="$(basename "$s")"
  [ "$name" = "readme_smoke_test.sh" ] && continue
  grep -q -- "$name" "$README" || fail "scripts/$name exists but is never mentioned in README.md"
done

# At least one real, copy-pasteable command (not just prose describing what
# to do) -- a fenced code block containing a shell invocation.
if ! grep -qE '^\s*(bash |\./|flux |cd )' "$README"; then
  fail "no literal runnable command (e.g. 'bash scripts/install.sh', './scripts/run_all.sh <alloc-id>') found in README.md"
fi

# A verification step: the reader needs to know what "reproduced" means --
# some pointer back to the numbers in REPORT.md to compare against.
grep -qi "REPORT.md" "$README" || fail "no pointer back to REPORT.md to compare reproduced numbers against"

if [ "$FAIL" -ne 0 ]; then
  echo "README smoke test: FAILED -- see MISSING lines above" >&2
  exit 1
fi
echo "README smoke test: PASSED -- every script is named, config.ini/WORKSPACE_ROOT explained, a runnable command and a verification pointer are both present."
