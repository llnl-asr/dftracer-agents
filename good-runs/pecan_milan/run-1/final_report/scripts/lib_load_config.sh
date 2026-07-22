#!/bin/bash
# Source this from every other script: loads config.ini and validates
# WORKSPACE_ROOT was actually set. No script other than this one should
# ever contain a hardcoded absolute path -- everything comes from config.ini.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${CONFIG_FILE:-$HERE/../config.ini}"

if [ ! -f "$CONFIG_FILE" ]; then
  echo "ERROR: config file not found: $CONFIG_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$CONFIG_FILE"
set +a

if [ -z "$WORKSPACE_ROOT" ] || [ "$WORKSPACE_ROOT" = "/path/to/your/session/workspace" ]; then
  echo "ERROR: set WORKSPACE_ROOT in $CONFIG_FILE to your actual session workspace path before running." >&2
  exit 1
fi

WS="$WORKSPACE_ROOT"
export WS
export CC="${CC_CMD:-cc}"
export CXX="${CXX_CMD:-CC}"

# OUTPUT_ROOT: where run scripts write their own dataset/traces output.
# Defaults to WS itself; override (e.g. to a scratch validation directory,
# kept separate from the original session's own run data) via config.ini or
# the environment before sourcing this file.
OUTPUT_ROOT="${OUTPUT_ROOT:-$WS}"
mkdir -p "$OUTPUT_ROOT"
export OUTPUT_ROOT
