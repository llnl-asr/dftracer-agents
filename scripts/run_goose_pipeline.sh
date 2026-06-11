#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
PYTHON_BIN="${VENV_DIR}/bin/python"
RECIPE_PATH="${ROOT_DIR}/goose/recipes/00_dftracer_pipeline.yaml"
STAGE_TIMEOUT_SECONDS="${DFTRACER_GOOSE_STAGE_TIMEOUT_SECONDS:-120}"

NAME="${DFTRACER_PIPELINE_NAME:-ior}"
REPO_URL="${DFTRACER_PIPELINE_REPO_URL:-https://github.com/hpc/ior}"
REPO_REF="${DFTRACER_PIPELINE_REPO_REF:-4.0.0}"
LANGUAGE="${DFTRACER_PIPELINE_LANGUAGE:-cpp}"
WORKSPACE_ROOT="${DFTRACER_PIPELINE_WORKSPACE_ROOT:-${ROOT_DIR}/workspaces/${NAME}}"
REPO_DIR="${DFTRACER_PIPELINE_REPO_DIR:-${WORKSPACE_ROOT}/source/${NAME}}"
VENV_PREFIX="${DFTRACER_PIPELINE_VENV_DIR:-${WORKSPACE_ROOT}/venv}"
TRACE_DIR="${DFTRACER_PIPELINE_TRACE_DIR:-${WORKSPACE_ROOT}/traces/terminal_default}"
POST_DIR="${DFTRACER_PIPELINE_POST_DIR:-${WORKSPACE_ROOT}/artifacts/terminal_default/postprocess}"
COMPACTED_TRACE_DIR="${DFTRACER_PIPELINE_COMPACTED_TRACE_DIR:-${POST_DIR}/compacted}"
ANALYSIS_DIR="${DFTRACER_PIPELINE_ANALYSIS_DIR:-${WORKSPACE_ROOT}/artifacts/terminal_default/analysis}"
FEEDBACK_DB="${DFTRACER_GOOSE_FEEDBACK_DB:-}"
FEEDBACK_PROMPT_MODE="${DFTRACER_GOOSE_FEEDBACK_PROMPT:-auto}"

_restore_if_set() {
  local name="$1"
  local value="$2"
  if [[ -n "${value}" ]]; then
    export "${name}=${value}"
  fi
}

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "venv python not found at ${PYTHON_BIN}. Run ./scripts/install.sh first."
  exit 1
fi

PRESET_LIVAI_BASE_URL="${LIVAI_BASE_URL:-}"
PRESET_LIVAI_API_KEY="${LIVAI_API_KEY:-}"
PRESET_LIVAI_MODEL="${LIVAI_MODEL:-}"
PRESET_OPENAI_BASE_URL="${OPENAI_BASE_URL:-}"
PRESET_OPENAI_API_KEY="${OPENAI_API_KEY:-}"
PRESET_OPENAI_MODEL="${OPENAI_MODEL:-}"
PRESET_GOOSE_PROVIDER="${GOOSE_PROVIDER:-}"
PRESET_GOOSE_MODEL="${GOOSE_MODEL:-}"

if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "${ROOT_DIR}/.env"
  set +a
fi

_restore_if_set LIVAI_BASE_URL "${PRESET_LIVAI_BASE_URL}"
_restore_if_set LIVAI_MODEL "${PRESET_LIVAI_MODEL}"
_restore_if_set OPENAI_BASE_URL "${PRESET_OPENAI_BASE_URL}"
_restore_if_set OPENAI_MODEL "${PRESET_OPENAI_MODEL}"
_restore_if_set GOOSE_PROVIDER "${PRESET_GOOSE_PROVIDER}"
_restore_if_set GOOSE_MODEL "${PRESET_GOOSE_MODEL}"

if [[ -n "${LIVAI_API_KEY:-}" && -z "${OPENAI_API_KEY:-}" ]]; then
  export OPENAI_API_KEY="${LIVAI_API_KEY}"
fi
if [[ -z "${PRESET_OPENAI_BASE_URL}" && -n "${PRESET_LIVAI_BASE_URL}" ]]; then
  export OPENAI_BASE_URL="${PRESET_LIVAI_BASE_URL}"
elif [[ -n "${LIVAI_BASE_URL:-}" && -z "${OPENAI_BASE_URL:-}" ]]; then
  export OPENAI_BASE_URL="${LIVAI_BASE_URL}"
fi
if [[ -z "${PRESET_OPENAI_MODEL}" && -n "${PRESET_LIVAI_MODEL}" ]]; then
  export OPENAI_MODEL="${PRESET_LIVAI_MODEL}"
elif [[ -n "${LIVAI_MODEL:-}" && -z "${OPENAI_MODEL:-}" ]]; then
  export OPENAI_MODEL="${LIVAI_MODEL}"
fi

if [[ -n "${OPENAI_BASE_URL:-}" ]]; then
  if [[ "${OPENAI_BASE_URL}" =~ ^(https?://[^/]+)(/(.*))?$ ]]; then
    if [[ -z "${OPENAI_HOST:-}" ]]; then
      export OPENAI_HOST="${BASH_REMATCH[1]}"
    fi
    if [[ -z "${OPENAI_BASE_PATH:-}" ]]; then
      base_path="${BASH_REMATCH[3]:-}"
      base_path="${base_path#/}"
      if [[ -z "${base_path}" || "${base_path}" == "v1" ]]; then
        export OPENAI_BASE_PATH="v1/chat/completions"
      elif [[ "${base_path}" == */chat/completions || "${base_path}" == */responses ]]; then
        export OPENAI_BASE_PATH="${base_path}"
      else
        export OPENAI_BASE_PATH="${base_path}/chat/completions"
      fi
    fi
  fi
fi

if [[ -z "${GOOSE_DISABLE_KEYRING:-}" ]]; then
  export GOOSE_DISABLE_KEYRING=1
fi

if [[ -n "${OPENAI_MODEL:-}" && -z "${GOOSE_MODEL:-}" ]]; then
  export GOOSE_MODEL="${OPENAI_MODEL}"
fi
if [[ -z "${GOOSE_PROVIDER:-}" ]]; then
  if [[ -n "${OPENAI_BASE_URL:-}" || -n "${OPENAI_API_KEY:-}" ]]; then
    export GOOSE_PROVIDER="openai"
  fi
fi

export PYTHONUNBUFFERED=1
export DFTRACER_GOOSE_STAGE_TIMEOUT_SECONDS="${STAGE_TIMEOUT_SECONDS}"

mkdir -p "${ROOT_DIR}/.cache/goose/pipeline_contexts" "${TRACE_DIR}" "${POST_DIR}" "${COMPACTED_TRACE_DIR}" "${ANALYSIS_DIR}"

echo "[goose-pipeline] recipe: ${RECIPE_PATH}" >&2
echo "[goose-pipeline] environment: OPENAI_BASE_URL=$([[ -n "${OPENAI_BASE_URL:-}" ]] && printf set || printf missing), OPENAI_MODEL=${OPENAI_MODEL:-missing}, OPENAI_API_KEY=$([[ -n "${OPENAI_API_KEY:-}" ]] && printf set || printf missing), LIVAI_BASE_URL=$([[ -n "${LIVAI_BASE_URL:-}" ]] && printf set || printf missing), LIVAI_MODEL=${LIVAI_MODEL:-missing}, LIVAI_API_KEY=$([[ -n "${LIVAI_API_KEY:-}" ]] && printf set || printf missing)" >&2
echo "[goose-pipeline] workspace_root: ${WORKSPACE_ROOT}" >&2
echo "[goose-pipeline] repo_dir: ${REPO_DIR}" >&2
echo "[goose-pipeline] venv_dir: ${VENV_PREFIX}" >&2
echo "[goose-pipeline] trace_dir: ${TRACE_DIR}" >&2
echo "[goose-pipeline] stage_timeout_seconds: ${STAGE_TIMEOUT_SECONDS}" >&2

cmd=(
  "${PYTHON_BIN}" -m dftracer_agents.cli goose-pipeline
  --name "${NAME}"
  --repo-url "${REPO_URL}"
  --repo-ref "${REPO_REF}"
  --language "${LANGUAGE}"
  --workspace-root "${WORKSPACE_ROOT}"
  --repo-dir "${REPO_DIR}"
  --venv-dir "${VENV_PREFIX}"
  --trace-dir "${TRACE_DIR}"
  --post-dir "${POST_DIR}"
  --compacted-trace-dir "${COMPACTED_TRACE_DIR}"
  --analysis-dir "${ANALYSIS_DIR}"
)

if [[ -n "${FEEDBACK_DB}" ]]; then
  cmd+=(--feedback-db "${FEEDBACK_DB}")
fi
case "${FEEDBACK_PROMPT_MODE,,}" in
  1|true|yes|on)
    cmd+=(--feedback-prompt)
    ;;
  0|false|no|off)
    cmd+=(--no-feedback-prompt)
    ;;
esac

echo "[goose-pipeline] command: ${cmd[*]}" >&2
exec "${cmd[@]}"
