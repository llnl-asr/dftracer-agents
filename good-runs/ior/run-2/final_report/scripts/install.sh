#!/bin/bash
# Rebuild the session's dependencies and application from scratch.
# Edit ../config.ini (WORKSPACE_ROOT) before running this.
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

# dftracer install prefix used by this session (for reference only --
# re-install from source into $WS, never reuse a path from another machine):
#   $PROJECT_ROOT/workspaces/ior/20260724_175545/install_dftracer/lib/python3.13/site-packages/dftracer/lib64

# TODO: fill in the exact build commands for ior.
# See ../patches/annotated.patch for the source-level changes and
# ../plan/pipeline_plan.md for the build flags this session used.
echo "EDIT scripts/install.sh with this app's real build commands" >&2
exit 1
