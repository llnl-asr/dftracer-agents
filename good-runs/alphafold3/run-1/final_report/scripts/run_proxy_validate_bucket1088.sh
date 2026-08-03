#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib_load_config.sh"

set -e
export CONFIG_FILE="${WS}/final_report/config.ini"
bash ${WS}/final_report/scripts/run_verify_1e66_bucket1088.sh
