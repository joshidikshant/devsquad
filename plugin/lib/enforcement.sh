#!/usr/bin/env bash
# lib/enforcement.sh -- Enforcement utilities for DevSquad delegation
# Sourced by hooks and other scripts. Do not execute directly.
set -euo pipefail

# Get enforcement mode from config (advisory or strict)
get_enforcement_mode() {
  local project_dir="${CLAUDE_PROJECT_DIR:-.}"
  local config_file="${project_dir}/.devsquad/config.json"

  if [[ ! -f "$config_file" ]]; then
    echo "advisory"
    return
  fi

  if command -v jq &>/dev/null; then
    local mode
    mode=$(jq -r '.enforcement_mode // "advisory"' "$config_file" 2>/dev/null)
    echo "${mode:-advisory}"
  else
    # Fallback: grep for enforcement_mode
    local mode
    mode=$(grep -o '"enforcement_mode"[[:space:]]*:[[:space:]]*"[^"]*"' "$config_file" 2>/dev/null | cut -d'"' -f4)
    echo "${mode:-advisory}"
  fi
}

# Log delegation suggestion
log_delegation() {
  local tool_name="$1"
  local agent="$2"
  local mode="$3"
  local project_dir="${CLAUDE_PROJECT_DIR:-.}"
  local log_dir="${project_dir}/.devsquad/logs"
  local log_file="${log_dir}/delegation.log"
  local timestamp
  timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

  mkdir -p "$log_dir"
  echo "${timestamp} | delegation_suggested | ${tool_name} | ${agent} | ${mode}" >> "$log_file"
}

# Log advisory suggestion (when hook suggests delegation)
log_suggestion() {
  local tool_name="$1"
  local agent="$2"
  local project_dir="${CLAUDE_PROJECT_DIR:-.}"
  local log_dir="${project_dir}/.devsquad/logs"
  local log_file="${log_dir}/compliance.log"
  local timestamp
  timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

  mkdir -p "$log_dir"
  echo "${timestamp} | advisory_suggested | ${tool_name} | ${agent} | suggested" >> "$log_file"
}

# Log actual override (when user proceeds despite suggestion)
# Currently: called from advisory mode in pre-tool-use.sh
# TODO: Move to PostToolUse hook for accurate tracking once available
log_override() {
  local tool_name="$1"
  local agent="$2"
  local project_dir="${CLAUDE_PROJECT_DIR:-.}"
  local log_dir="${project_dir}/.devsquad/logs"
  local log_file="${log_dir}/compliance.log"
  local timestamp
  timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

  mkdir -p "$log_dir"
  echo "${timestamp} | advisory_override | ${tool_name} | ${agent} | allowed" >> "$log_file"
}

# Increment session-scoped read counter
increment_read_counter() {
  local project_dir="${CLAUDE_PROJECT_DIR:-.}"
  local state_dir="${project_dir}/.devsquad"
  local counter_file="${state_dir}/read_count"
  local temp_file="${counter_file}.tmp.$$"

  mkdir -p "$state_dir"

  local current_count=0
  if [[ -f "$counter_file" ]]; then
    current_count=$(cat "$counter_file" 2>/dev/null || echo "0")
  fi

  local new_count=$((current_count + 1))
  echo "$new_count" > "$temp_file"
  mv "$temp_file" "$counter_file"

  echo "$new_count"
}

# Get current read count
get_read_count() {
  local project_dir="${CLAUDE_PROJECT_DIR:-.}"
  local state_dir="${project_dir}/.devsquad"
  local counter_file="${state_dir}/read_count"

  if [[ -f "$counter_file" ]]; then
    cat "$counter_file" 2>/dev/null || echo "0"
  else
    echo "0"
  fi
}

# Check if agent CLI is available
# Takes an agent name (e.g., "gemini-reader", "codex-drafter") and returns 0 if CLI exists, 1 if not
check_agent_cli_available() {
  local agent="$1"
  local cli_name=""

  # Extract CLI name from agent name
  if [[ "$agent" == gemini* ]]; then
    cli_name="gemini"
  elif [[ "$agent" == codex* ]]; then
    cli_name="codex"
  else
    # Unknown agent type - assume unavailable
    return 1
  fi

  # Check if CLI is available
  if command -v "$cli_name" &>/dev/null; then
    return 0
  else
    return 1
  fi
}

# Detect if a bash command is test-related
# Returns 0 (true) if command looks like test execution/generation
is_test_command() {
  local cmd="$1"
  local cmd_lower
  cmd_lower=$(printf '%s' "$cmd" | tr '[:upper:]' '[:lower:]')

  # Match common test patterns
  case "$cmd_lower" in
    *"npm test"*|*"npm run test"*|*"npx jest"*|*"npx vitest"*|*"npx mocha"*)
      return 0 ;;
    *pytest*|*"python -m pytest"*|*"python -m unittest"*)
      return 0 ;;
    *"go test"*|*"cargo test"*|*"mix test"*)
      return 0 ;;
    *"write test"*|*"generate test"*|*"create test"*)
      return 0 ;;
    *)
      return 1 ;;
  esac
}

# Log strict mode degradation when CLI is missing
log_degradation() {
  local tool_name="$1"
  local agent="$2"
  local reason="$3"
  local project_dir="${CLAUDE_PROJECT_DIR:-.}"
  local log_dir="${project_dir}/.devsquad/logs"
  local log_file="${log_dir}/compliance.log"
  local timestamp
  timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

  mkdir -p "$log_dir"
  echo "${timestamp} | strict_degraded | ${tool_name} | ${agent} | ${reason}" >> "$log_file"
}

# Estimate token savings for delegating a file read to Gemini
# Uses file size heuristic: ~4 bytes per token for text files
# Returns human-readable estimate (e.g., "~12K tokens")
estimate_token_savings() {
  local file_path="$1"

  # If file doesn't exist or path is generic, return generic estimate
  if [[ ! -f "$file_path" ]]; then
    echo "~5-20K tokens"
    return
  fi

  local file_size
  file_size=$(stat -f%z "$file_path" 2>/dev/null || stat -c%s "$file_path" 2>/dev/null || echo "0")

  if [[ "$file_size" -eq 0 ]]; then
    echo "~5-20K tokens"
    return
  fi

  # Approximate: 4 bytes per token
  local estimated_tokens=$((file_size / 4))

  # Format as human-readable
  if [[ "$estimated_tokens" -ge 1000000 ]]; then
    local millions=$((estimated_tokens / 1000000))
    echo "~${millions}M tokens"
  elif [[ "$estimated_tokens" -ge 1000 ]]; then
    local thousands=$((estimated_tokens / 1000))
    echo "~${thousands}K tokens"
  else
    echo "~${estimated_tokens} tokens"
  fi
}

# Estimate cumulative savings for all reads above threshold
estimate_session_savings() {
  local project_dir="${CLAUDE_PROJECT_DIR:-.}"
  local read_count_file="${project_dir}/.devsquad/read_count"
  local threshold="${1:-3}"

  if [[ ! -f "$read_count_file" ]]; then
    echo "~5-20K tokens"
    return
  fi

  local count
  count=$(cat "$read_count_file" 2>/dev/null || echo "0")
  local excess=$((count - threshold))

  if [[ "$excess" -le 0 ]]; then
    echo "~5-20K tokens"
    return
  fi

  # Rough estimate: 8K tokens per file on average
  local estimated=$((excess * 8))
  echo "~${estimated}K tokens"
}
