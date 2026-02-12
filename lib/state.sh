#!/usr/bin/env bash
# lib/state.sh -- State management for DevSquad
# Sourced by hooks and other scripts. Do not execute directly.

# Initialize state directory structure
init_state_dir() {
  local project_dir="${CLAUDE_PROJECT_DIR:-.}"
  local state_dir="${project_dir}/.devsquad"

  mkdir -p "${state_dir}"
  mkdir -p "${state_dir}/usage"
  mkdir -p "${state_dir}/logs"

  echo "${state_dir}"
}

# Read a JSON file, return empty object if not found
read_state() {
  local file_path="$1"
  if [[ -f "$file_path" ]]; then
    cat "$file_path"
  else
    echo "{}"
  fi
}

# Write JSON to a state file atomically (write to temp, then rename)
write_state() {
  local file_path="$1"
  local json_content="$2"
  local temp_file="${file_path}.tmp.$$"

  echo "$json_content" > "$temp_file"
  mv "$temp_file" "$file_path"
}

# Update a key in a JSON state file using jq
# Falls back to writing the whole object if jq is not available
update_state_key() {
  local file_path="$1"
  local key="$2"
  local value="$3"

  if command -v jq &>/dev/null; then
    local current
    current=$(read_state "$file_path")
    local updated
    updated=$(echo "$current" | jq --arg k "$key" --argjson v "$value" 'setpath($k | split("."); $v)')
    write_state "$file_path" "$updated"
  else
    # Without jq, we cannot safely merge JSON. Log warning.
    echo "WARNING: jq not available, cannot update state key" >&2
  fi
}

# Get a value from a JSON state file
get_state_key() {
  local file_path="$1"
  local key="$2"

  if command -v jq &>/dev/null; then
    read_state "$file_path" | jq -r --arg k "$key" 'getpath($k | split(".")) // empty'
  else
    echo ""
  fi
}

# Create default config if none exists
ensure_config() {
  local state_dir="$1"
  local config_file="${state_dir}/config.json"

  if [[ ! -f "$config_file" ]]; then
    write_state "$config_file" '{
  "version": 1,
  "enforcement_mode": "advisory",
  "default_routes": {
    "research": "gemini",
    "reading": "gemini",
    "code_generation": "codex",
    "testing": "codex",
    "synthesis": "self"
  },
  "preferences": {
    "gemini_word_limit": 300,
    "codex_line_limit": 50,
    "auto_suggest": true
  }
}'
  fi
}

# Create default session state
init_session_state() {
  local state_dir="$1"
  local state_file="${state_dir}/state.json"
  local timestamp
  timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

  write_state "$state_file" "{
  \"version\": 1,
  \"session\": {
    \"started\": \"${timestamp}\",
    \"zone\": \"green\"
  },
  \"cli\": {},
  \"stats\": {
    \"gemini_calls\": 0,
    \"codex_calls\": 0,
    \"self_calls\": 0
  }
}"
}

# Update agent statistics (call count and error count)
update_agent_stats() {
  local state_dir="$1"
  local agent_name="$2"
  local success="${3:-true}"
  local state_file="${state_dir}/state.json"

  if command -v jq &>/dev/null; then
    local current
    current=$(read_state "$state_file")
    local calls_key="${agent_name}_calls"
    local errors_key="${agent_name}_errors"

    # Increment call count
    local updated
    updated=$(echo "$current" | jq --arg k "$calls_key" '.stats[$k] = (.stats[$k] // 0) + 1')

    # If not successful, increment error count
    if [[ "$success" == "false" ]]; then
      updated=$(echo "$updated" | jq --arg k "$errors_key" '.stats[$k] = (.stats[$k] // 0) + 1')
    fi

    write_state "$state_file" "$updated"
  else
    echo "WARNING: jq not available, cannot update agent stats" >&2
  fi
}

# Record rate limit cooldown for an agent
record_rate_limit() {
  local state_dir="$1"
  local agent_name="$2"
  local cooldown_file="${state_dir}/cooldown_${agent_name}"
  local current_epoch
  current_epoch=$(date +%s)
  local cooldown_until=$((current_epoch + 120))

  echo "$cooldown_until" > "$cooldown_file"
}

# Check if agent is in rate limit cooldown
check_rate_limit() {
  local state_dir="$1"
  local agent_name="$2"
  local cooldown_file="${state_dir}/cooldown_${agent_name}"

  if [[ ! -f "$cooldown_file" ]]; then
    echo "false"
    return
  fi

  local cooldown_until
  cooldown_until=$(cat "$cooldown_file" 2>/dev/null || echo "0")
  local current_epoch
  current_epoch=$(date +%s)

  if [[ "$current_epoch" -lt "$cooldown_until" ]]; then
    echo "true"
  else
    echo "false"
  fi
}

# Get snapshot contents (convenience wrapper)
get_snapshot() {
  local state_dir="$1"
  local snapshot_file="${state_dir}/snapshot.json"
  read_state "$snapshot_file"
}
