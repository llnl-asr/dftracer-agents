#!/bin/bash
# PreToolUse guard for Bash: blocks rm -rf / mpifileutils drm unless every
# absolute path argument is under an allowed root:
#   - a session workspace dir (must contain "/workspaces/") under
#     /usr/workspace, /usr/WS1, /usr/WS2
#   - anywhere under /p/lustre5 (Lustre session/output dirs)
# Relative paths are allowed through (already scoped by settings.json's
# Write/Edit(workspaces/*) rules and normal permission prompts).
set -euo pipefail

input="$(cat)"
cmd="$(printf '%s' "$input" | jq -r '.tool_input.command // empty')"

[[ -z "$cmd" ]] && exit 0

# Only inspect commands that look like a destructive rm or an mpifileutils drm call.
if ! printf '%s' "$cmd" | grep -qE '(^|[;&|]|[[:space:]])(rm[[:space:]]+(-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*|-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*)|drm)([[:space:]]|$)'; then
  exit 0
fi

bad=""
for tok in $cmd; do
  case "$tok" in
    /*)
      case "$tok" in
        /p/lustre5/*|/p/lustre5)
          ;;
        /usr/workspace/*/workspaces/*|/usr/WS1/*/workspaces/*|/usr/WS2/*/workspaces/*)
          ;;
        *)
          bad="$tok"
          ;;
      esac
      ;;
  esac
done

if [[ -n "$bad" ]]; then
  reason="Blocked: rm/drm target '$bad' is outside allowed roots (session workspaces under /usr/workspace|/usr/WS1|/usr/WS2, or /p/lustre5). Use a session workspace path or a /p/lustre5 path."
  jq -n --arg reason "$reason" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$reason}}'
fi

exit 0
