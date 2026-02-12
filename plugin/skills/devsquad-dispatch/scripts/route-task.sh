#!/usr/bin/env bash
set -euo pipefail

# Resolve paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PLUGIN_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

# Source routing library
source "${PLUGIN_ROOT}/lib/routing.sh"

# Validate input
if [[ $# -lt 1 ]] || [[ -z "$1" ]]; then
  echo '{"error":"Usage: route-task.sh <task_description>","recommended_agent":"self","command":"","reason":"No task description provided."}'
  exit 1
fi

# Route the task
route_task "$1"
