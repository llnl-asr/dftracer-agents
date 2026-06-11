#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
PYTHON_BIN="${VENV_DIR}/bin/python"

if [[ ! -d "${VENV_DIR}" ]]; then
  echo "Missing virtual environment at ${VENV_DIR}. Run ./scripts/install.sh first."
  exit 1
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Missing python interpreter at ${PYTHON_BIN}. Run ./scripts/install.sh first."
  exit 1
fi

exec "${PYTHON_BIN}" -m dftracer_agents.mcp_servers.server
