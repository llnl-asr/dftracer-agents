#!/usr/bin/env bash
# set-models.sh — replace {{ORCHESTRATOR_MODEL}} and {{ACTION_MODEL}} placeholders
# in opencode.json and all agent/command files under opencode/agents/ and opencode/commands/.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCHESTRATOR_DEFAULT="ollama/qwen3.5:397b-cloud"
ACTION_DEFAULT="ollama/kimi-k2.7-code:cloud"

ORCHESTRATOR="${ORCHESTRATOR_DEFAULT}"
ACTION="${ACTION_DEFAULT}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --orchestrator)
      ORCHESTRATOR="$2"
      shift 2
      ;;
    --action)
      ACTION="$2"
      shift 2
      ;;
    -h|--help)
      cat <<EOF
Usage: $0 [--orchestrator MODEL] [--action MODEL]

Replace {{ORCHESTRATOR_MODEL}} and {{ACTION_MODEL}} placeholders in:
  - opencode/opencode.json
  - opencode/agents/*.md
  - opencode/agents/subagents/*.md
  - opencode/commands/*.md

Defaults:
  --orchestrator ${ORCHESTRATOR_DEFAULT}
  --action       ${ACTION_DEFAULT}
EOF
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

if [[ "${ORCHESTRATOR}" == "{{ORCHESTRATOR_MODEL}}" || "${ACTION}" == "{{ACTION_MODEL}}" ]]; then
  echo "Error: --orchestrator and --action cannot be literal placeholder strings." >&2
  exit 1
fi

FILES=(
  "${SCRIPT_DIR}/opencode.json"
)

while IFS= read -r -d '' file; do
  FILES+=("$file")
done < <(find "${SCRIPT_DIR}/agents" -type f -name '*.md' -print0 2>/dev/null || true)

while IFS= read -r -d '' file; do
  FILES+=("$file")
done < <(find "${SCRIPT_DIR}/commands" -type f -name '*.md' -print0 2>/dev/null || true)

REPLACED=0
for file in "${FILES[@]}"; do
  if [[ -f "$file" ]]; then
    sed -i \
      -e "s|{{ORCHESTRATOR_MODEL}}|${ORCHESTRATOR}|g" \
      -e "s|{{ACTION_MODEL}}|${ACTION}|g" \
      "$file"
    echo "Updated: $file"
    REPLACED=$((REPLACED + 1))
  fi
done

echo "Replaced placeholders in ${REPLACED} file(s)."
echo "  ORCHESTRATOR_MODEL=${ORCHESTRATOR}"
echo "  ACTION_MODEL=${ACTION}"

REMAINING_FILES=()
for file in "${FILES[@]}"; do
  if [[ -f "$file" ]] && grep -qE '\{\{ORCHESTRATOR_MODEL\}\}|\{\{ACTION_MODEL\}\}' "$file" 2>/dev/null; then
    REMAINING_FILES+=("$file")
  fi
done

if [[ ${#REMAINING_FILES[@]} -gt 0 ]]; then
  echo "Warning: model placeholders remain in the following file(s):" >&2
  for f in "${REMAINING_FILES[@]}"; do
    echo "  - $f" >&2
  done
  exit 1
fi
