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

# Read config file first
project_dir="${CLAUDE_PROJECT_DIR:-.}"
config_file="${project_dir}/.devsquad/config.json"

if [[ ! -f "$config_file" ]]; then
  echo "Error: Config file not found at ${config_file}"
  echo "Run /devsquad:setup to initialize configuration."
  exit 1
fi

# Build jq path expression from dotted key (e.g., "preferences.gemini_word_limit" -> ".preferences.gemini_word_limit")
jq_path=".${key}"

# Validate key exists in config
if ! jq -e "$jq_path" "$config_file" >/dev/null 2>&1; then
  echo "Error: Unknown config key: ${key}"
  echo "Valid keys can be found by running: /devsquad:config"
  exit 1
fi

# Determine value type from existing config
value_type=$(jq -r "$jq_path | type" "$config_file")

# Validate value based on type and key-specific constraints
case "$value_type" in
  number)
    if ! [[ "$value" =~ ^[0-9]+$ ]]; then
      echo "Error: Invalid value for ${key}: ${value}. Expected: numeric value"
      exit 1
    fi
    ;;
  boolean)
    if [[ "$value" != "true" && "$value" != "false" ]]; then
      echo "Error: Invalid value for ${key}: ${value}. Expected: true|false"
      exit 1
    fi
    ;;
  string)
    case "$key" in
      enforcement_mode)
        if [[ "$value" != "advisory" && "$value" != "strict" ]]; then
          echo "Error: Invalid value for enforcement_mode: ${value}. Expected: advisory|strict"
          exit 1
        fi
        ;;
      default_routes.*)
        if [[ "$value" != "gemini" && "$value" != "codex" && "$value" != "self" ]]; then
          echo "Error: Invalid value for ${key}: ${value}. Expected: gemini|codex|self"
          exit 1
        fi
        ;;
    esac
    ;;
esac

# Update config atomically using jq
temp_file="${config_file}.tmp.$$"

if [[ "$value_type" == "number" || "$value_type" == "boolean" ]]; then
  jq "$jq_path = ${value}" "$config_file" > "$temp_file"
else
  jq "$jq_path = \"${value}\"" "$config_file" > "$temp_file"
fi

mv "$temp_file" "$config_file"

echo "Updated ${key} to ${value}"
