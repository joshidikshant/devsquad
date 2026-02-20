#!/usr/bin/env bash
set -euo pipefail

# Prevent recursive hook firing from agent subshells
if [[ "${DEVSQUAD_HOOK_DEPTH:-0}" -ge 1 ]]; then
  echo '{"hookSpecificOutput":{"hookEventName":"PreCompact"}}'
  exit 0
fi

# Resolve paths relative to script location (portable)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PLUGIN_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source libraries
source "${PLUGIN_ROOT}/lib/state.sh"
source "${PLUGIN_ROOT}/lib/cli-detect.sh"

# Initialize state directory
STATE_DIR=$(init_state_dir)

# Read current state and config
CURRENT_STATE=$(read_state "${STATE_DIR}/state.json")
CURRENT_CONFIG=$(read_state "${STATE_DIR}/config.json")

# Get timestamp
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Discover agents from plugin directory
AGENTS_DIR="${PLUGIN_ROOT}/agents"
AGENT_NAMES=""
if [[ -d "$AGENTS_DIR" ]]; then
  # Parse agent names from YAML frontmatter (grep for 'name:', extract value)
  # Compatible with bash 3, no arrays
  for agent_file in "${AGENTS_DIR}"/*.md; do
    if [[ -f "$agent_file" ]]; then
      # Extract name from YAML frontmatter (between --- markers)
      # Use awk to find name: field value
      agent_name=$(awk '/^---$/,/^---$/ {if(/^name:/) {sub(/^name: */, ""); print; exit}}' "$agent_file" | tr -d '"' | xargs)
      if [[ -n "$agent_name" ]]; then
        if [[ -z "$AGENT_NAMES" ]]; then
          AGENT_NAMES="\"${agent_name}\""
        else
          AGENT_NAMES="${AGENT_NAMES}, \"${agent_name}\""
        fi
      fi
    fi
  done
fi

# Build snapshot JSON
# If jq available, use it; otherwise build manually
if command -v jq &>/dev/null; then
  # Use jq to build snapshot
  SNAPSHOT=$(jq -n \
    --arg ts "$TIMESTAMP" \
    --argjson config "$CURRENT_CONFIG" \
    --argjson state "$CURRENT_STATE" \
    --argjson agents "[${AGENT_NAMES:-}]" \
    '{
      timestamp: $ts,
      snapshot_version: 1,
      config: $config,
      state: $state,
      agents: $agents
    }')
else
  # Build JSON manually (jq-optional fallback)
  SNAPSHOT="{
  \"timestamp\": \"${TIMESTAMP}\",
  \"snapshot_version\": 1,
  \"config\": ${CURRENT_CONFIG},
  \"state\": ${CURRENT_STATE},
  \"agents\": [${AGENT_NAMES:-}]
}"
fi

# Write snapshot atomically
SNAPSHOT_FILE="${STATE_DIR}/snapshot.json"
write_state "$SNAPSHOT_FILE" "$SNAPSHOT"

# Output valid JSON hook response
echo '{"hookSpecificOutput":{"hookEventName":"PreCompact"}}'
