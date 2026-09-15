#!/usr/bin/env bash
# Print the livai API key on stdout. Used as Claude Code's `apiKeyHelper`.
#
# The key lives in phronix's .env (mode 0600, outside this repo) and is read at
# launch. It is deliberately NOT written into .claude/settings.local.json:
# that file is tracked by git, so a key placed there would be committed.
set -euo pipefail
ENV_FILE="${PHRONIX_ENV:-/usr/WS2/haridev/phronix/.env}"
[ -r "$ENV_FILE" ] || { echo "livai-key: cannot read $ENV_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a
[ -n "${OPENAI_API_KEY:-}" ] || { echo "livai-key: OPENAI_API_KEY unset in $ENV_FILE" >&2; exit 1; }
printf '%s' "$OPENAI_API_KEY"
