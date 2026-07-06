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

# Validate a model name against `agy models` when possible. agy silently
# ignores unknown --model values, so storing an unverified name would fail
# silently at delegation time. Set DEVSQUAD_SKIP_MODEL_VALIDATION=1 to bypass
# (tests, offline use).
_validate_model_name() {
  local value="$1"
  if [[ -z "$value" ]]; then
    echo "Error: model name cannot be empty"
    return 1
  fi
  if [[ -n "${DEVSQUAD_SKIP_MODEL_VALIDATION:-}" ]] || ! command -v agy &>/dev/null; then
    return 0
  fi
  local models
  models=$(agy models 2>/dev/null || true)
  if [[ -z "$models" ]]; then
    return 0
  fi
  if ! printf '%s\n' "$models" | grep -qxF "$value"; then
    echo "Error: '$value' is not an exact entry of 'agy models'. Valid models:"
    printf '%s\n' "$models" | sed 's/^/  /'
    echo "(agy silently ignores unknown models, so unverifiable names are refused)"
    return 1
  fi
  return 0
}

# Build jq path expression from dotted key with quoted segments so
# hyphenated keys work (e.g. agent_models.gemini-reader -> ."agent_models"."gemini-reader")
jq_path=$(printf '%s' "$key" | awk -F. '{ for (i = 1; i <= NF; i++) printf ".\"%s\"", $i }')

# Validate key exists in config. Two subtleties:
# - `jq -e "$jq_path"` alone exits 1 for keys whose VALUE is false/null,
#   so present boolean keys would read as "unknown" — compare to null instead.
# - Known keys introduced after a config was created (schema additions) may
#   be created on first set; configs are not migrated in place.
if ! jq -e "${jq_path} != null" "$config_file" >/dev/null 2>&1; then
  case "$key" in
    default_routes.development|holdout_mode|preferences.gemini_model|preferences.codex_model|preferences.grok_model|agent_models.*)
      : ;; # known later-version key — allow creation below
    *)
      echo "Error: Unknown config key: ${key}"
      echo "Valid keys can be found by running: /devsquad:config"
      exit 1
      ;;
  esac
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
        if [[ "$value" != "gemini" && "$value" != "codex" && "$value" != "grok" && "$value" != "self" ]]; then
          echo "Error: Invalid value for ${key}: ${value}. Expected: gemini|codex|grok|self"
          exit 1
        fi
        ;;
      agent_models.gemini-*|preferences.gemini_model)
        _validate_model_name "$value" || exit 1
        ;;
      agent_models.*|preferences.grok_model|preferences.codex_model)
        # grok/codex CLIs surface bad model names themselves; only reject empty
        if [[ -z "$value" ]]; then
          echo "Error: model name cannot be empty"
          exit 1
        fi
        ;;
    esac
    ;;
  null)
    # Key is being created (known later-version key) — validate by key name
    case "$key" in
      default_routes.*)
        if [[ "$value" != "gemini" && "$value" != "codex" && "$value" != "grok" && "$value" != "self" ]]; then
          echo "Error: Invalid value for ${key}: ${value}. Expected: gemini|codex|grok|self"
          exit 1
        fi
        ;;
      holdout_mode)
        if [[ "$value" != "true" && "$value" != "false" ]]; then
          echo "Error: Invalid value for ${key}: ${value}. Expected: true|false"
          exit 1
        fi
        ;;
      agent_models.gemini-*|preferences.gemini_model)
        _validate_model_name "$value" || exit 1
        ;;
      agent_models.*|preferences.grok_model|preferences.codex_model)
        # grok/codex CLIs surface bad model names themselves; only reject empty
        if [[ -z "$value" ]]; then
          echo "Error: model name cannot be empty"
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
elif [[ "$value_type" == "null" && ( "$value" == "true" || "$value" == "false" ) ]]; then
  # Created boolean key must be stored as a boolean, not the string "true"
  jq "$jq_path = ${value}" "$config_file" > "$temp_file"
else
  jq "$jq_path = \"${value}\"" "$config_file" > "$temp_file"
fi

mv "$temp_file" "$config_file"

echo "Updated ${key} to ${value}"
