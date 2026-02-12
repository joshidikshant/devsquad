#!/usr/bin/env bash
# show-config.sh -- Display current DevSquad configuration

set -euo pipefail

# Resolve script directory and plugin root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

# Source required libraries
source "${PLUGIN_ROOT}/lib/state.sh"

# Initialize state directory
init_state_dir

# Read config file
project_dir="${CLAUDE_PROJECT_DIR:-.}"
config_file="${project_dir}/.devsquad/config.json"

if [[ ! -f "$config_file" ]]; then
  echo "Error: Config file not found at ${config_file}"
  echo "Run /devsquad:setup to initialize configuration."
  exit 1
fi

# Parse config using jq if available, otherwise fallback to grep
if command -v jq &>/dev/null; then
  # Extract all values using jq
  enforcement_mode=$(jq -r '.enforcement_mode // "advisory"' "$config_file")

  route_research=$(jq -r '.default_routes.research // "gemini"' "$config_file")
  route_reading=$(jq -r '.default_routes.reading // "gemini"' "$config_file")
  route_code_gen=$(jq -r '.default_routes.code_generation // "codex"' "$config_file")
  route_testing=$(jq -r '.default_routes.testing // "codex"' "$config_file")
  route_synthesis=$(jq -r '.default_routes.synthesis // "self"' "$config_file")

  gemini_word_limit=$(jq -r '.preferences.gemini_word_limit // 300' "$config_file")
  codex_line_limit=$(jq -r '.preferences.codex_line_limit // 50' "$config_file")
  auto_suggest=$(jq -r '.preferences.auto_suggest // true' "$config_file")
else
  # Fallback: use grep to extract values
  enforcement_mode=$(grep -o '"enforcement_mode":[[:space:]]*"[^"]*"' "$config_file" | sed 's/.*"\([^"]*\)".*/\1/' || echo "advisory")

  route_research=$(grep -o '"research":[[:space:]]*"[^"]*"' "$config_file" | sed 's/.*"\([^"]*\)".*/\1/' || echo "gemini")
  route_reading=$(grep -o '"reading":[[:space:]]*"[^"]*"' "$config_file" | sed 's/.*"\([^"]*\)".*/\1/' || echo "gemini")
  route_code_gen=$(grep -o '"code_generation":[[:space:]]*"[^"]*"' "$config_file" | sed 's/.*"\([^"]*\)".*/\1/' || echo "codex")
  route_testing=$(grep -o '"testing":[[:space:]]*"[^"]*"' "$config_file" | sed 's/.*"\([^"]*\)".*/\1/' || echo "codex")
  route_synthesis=$(grep -o '"synthesis":[[:space:]]*"[^"]*"' "$config_file" | sed 's/.*"\([^"]*\)".*/\1/' || echo "self")

  gemini_word_limit=$(grep -o '"gemini_word_limit":[[:space:]]*[0-9]*' "$config_file" | grep -o '[0-9]*$' || echo "300")
  codex_line_limit=$(grep -o '"codex_line_limit":[[:space:]]*[0-9]*' "$config_file" | grep -o '[0-9]*$' || echo "50")
  auto_suggest=$(grep -o '"auto_suggest":[[:space:]]*[a-z]*' "$config_file" | sed 's/.*:[[:space:]]*//' || echo "true")
fi

# Display formatted configuration
cat <<CONFIG

=== DevSquad Configuration ===

Enforcement Mode: ${enforcement_mode}

Default Routes:
  research:        ${route_research}
  reading:         ${route_reading}
  code_generation: ${route_code_gen}
  testing:         ${route_testing}
  synthesis:       ${route_synthesis}

Preferences:
  gemini_word_limit: ${gemini_word_limit}
  codex_line_limit:  ${codex_line_limit}
  auto_suggest:      ${auto_suggest}

Edit: /devsquad:config enforcement_mode=strict

CONFIG
