#!/usr/bin/env bash
# update-config.sh -- Update DevSquad configuration atomically

set -euo pipefail

# Resolve script directory and plugin root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

# Source required libraries
source "${PLUGIN_ROOT}/lib/state.sh"

# Initialize state directory
init_state_dir

# Check for jq requirement
if ! command -v jq &>/dev/null; then
  echo "Error: jq required for config updates. Install: brew install jq"
  exit 1
fi

# Parse argument
if [[ $# -lt 1 ]]; then
  echo "Usage: $0 key=value"
  echo "Example: $0 enforcement_mode=strict"
  exit 1
fi

arg="$1"

# Split on first equals sign
if [[ ! "$arg" =~ = ]]; then
  echo "Error: Invalid argument format. Expected key=value"
  exit 1
fi

key="${arg%%=*}"
value="${arg#*=}"

# Validate key and determine jq path
jq_path=""
value_type="string"

case "$key" in
  enforcement_mode)
    jq_path=".enforcement_mode"
    if [[ "$value" != "advisory" && "$value" != "strict" ]]; then
      echo "Error: Invalid value for enforcement_mode: ${value}. Expected: advisory|strict"
      exit 1
    fi
    ;;
  gemini_word_limit)
    jq_path=".preferences.gemini_word_limit"
    value_type="number"
    if ! [[ "$value" =~ ^[0-9]+$ ]]; then
      echo "Error: Invalid value for gemini_word_limit: ${value}. Expected: numeric value"
      exit 1
    fi
    ;;
  codex_line_limit)
    jq_path=".preferences.codex_line_limit"
    value_type="number"
    if ! [[ "$value" =~ ^[0-9]+$ ]]; then
      echo "Error: Invalid value for codex_line_limit: ${value}. Expected: numeric value"
      exit 1
    fi
    ;;
  auto_suggest)
    jq_path=".preferences.auto_suggest"
    value_type="boolean"
    if [[ "$value" != "true" && "$value" != "false" ]]; then
      echo "Error: Invalid value for auto_suggest: ${value}. Expected: true|false"
      exit 1
    fi
    ;;
  default_routes.research)
    jq_path=".default_routes.research"
    if [[ "$value" != "gemini" && "$value" != "codex" && "$value" != "self" ]]; then
      echo "Error: Invalid value for default_routes.research: ${value}. Expected: gemini|codex|self"
      exit 1
    fi
    ;;
  default_routes.reading)
    jq_path=".default_routes.reading"
    if [[ "$value" != "gemini" && "$value" != "self" ]]; then
      echo "Error: Invalid value for default_routes.reading: ${value}. Expected: gemini|self"
      exit 1
    fi
    ;;
  default_routes.code_generation)
    jq_path=".default_routes.code_generation"
    if [[ "$value" != "gemini" && "$value" != "codex" ]]; then
      echo "Error: Invalid value for default_routes.code_generation: ${value}. Expected: gemini|codex"
      exit 1
    fi
    ;;
  default_routes.testing)
    jq_path=".default_routes.testing"
    if [[ "$value" != "gemini" && "$value" != "codex" ]]; then
      echo "Error: Invalid value for default_routes.testing: ${value}. Expected: gemini|codex"
      exit 1
    fi
    ;;
  default_routes.synthesis)
    jq_path=".default_routes.synthesis"
    if [[ "$value" != "self" ]]; then
      echo "Error: Invalid value for default_routes.synthesis: ${value}. Expected: self"
      exit 1
    fi
    ;;
  *)
    echo "Error: Unknown config key: ${key}"
    echo "Valid keys: enforcement_mode, gemini_word_limit, codex_line_limit, auto_suggest,"
    echo "            default_routes.research, default_routes.reading, default_routes.code_generation,"
    echo "            default_routes.testing, default_routes.synthesis"
    exit 1
    ;;
esac

# Read config file
project_dir="${CLAUDE_PROJECT_DIR:-.}"
config_file="${project_dir}/.devsquad/config.json"

if [[ ! -f "$config_file" ]]; then
  echo "Error: Config file not found at ${config_file}"
  echo "Run /devsquad:setup to initialize configuration."
  exit 1
fi

# Update config atomically using jq
temp_file="${config_file}.tmp.$$"

if [[ "$value_type" == "number" ]]; then
  # Update as number (no quotes)
  jq "${jq_path} = ${value}" "$config_file" > "$temp_file"
elif [[ "$value_type" == "boolean" ]]; then
  # Update as boolean (no quotes)
  jq "${jq_path} = ${value}" "$config_file" > "$temp_file"
else
  # Update as string (with quotes)
  jq "${jq_path} = \"${value}\"" "$config_file" > "$temp_file"
fi

# Atomic move
mv "$temp_file" "$config_file"

echo "Updated ${key} to ${value}"
