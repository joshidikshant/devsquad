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

# Log a compliance event to the compliance log
# Usage: _log_compliance "event_type" "tool_name" "agent" "outcome"
_log_compliance() {
  local event_type="$1"
  local tool_name="$2"
  local agent="$3"
  local outcome="$4"
  local project_dir="${CLAUDE_PROJECT_DIR:-.}"
  local log_dir="${project_dir}/.devsquad/logs"
  local log_file="${log_dir}/compliance.log"
  local timestamp
  timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

  mkdir -p "$log_dir"
  echo "${timestamp} | ${event_type} | ${tool_name} | ${agent} | ${outcome}" >> "$log_file"
}

# Log advisory suggestion (when hook suggests delegation)
log_suggestion() {
  _log_compliance "advisory_suggested" "$1" "$2" "suggested"
}

# Log actual override (when user proceeds despite suggestion)
# TODO: Move to PostToolUse hook for accurate tracking once available
log_override() {
  _log_compliance "advisory_override" "$1" "$2" "allowed"
}

# Record delegation suggestion in session state for acceptance tracking
record_suggestion() {
  local tool_name="$1"
  local agent="$2"
  local file_path="$3"
  local project_dir="${CLAUDE_PROJECT_DIR:-.}"
  local state_file="${project_dir}/.devsquad/state.json"
  local timestamp
  timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

  if command -v jq &>/dev/null && [[ -f "$state_file" ]]; then
    local temp_file="${state_file}.tmp.$$"
    jq --arg ts "$timestamp" --arg tool "$tool_name" --arg agent "$agent" --arg fp "$file_path" \
      '.session.last_suggestion = {"timestamp": $ts, "tool": $tool, "agent": $agent, "file_path": $fp}' \
      "$state_file" > "$temp_file" && mv "$temp_file" "$state_file"
  fi
}

# Check if previous suggestion was accepted or declined based on next tool call
check_and_log_suggestion_outcome() {
  local current_tool="$1"
  local project_dir="${CLAUDE_PROJECT_DIR:-.}"
  local state_file="${project_dir}/.devsquad/state.json"

  if ! command -v jq &>/dev/null || [[ ! -f "$state_file" ]]; then
    return
  fi

  local last_suggestion
  last_suggestion=$(jq -r '.session.last_suggestion // empty' "$state_file" 2>/dev/null)
  if [[ -z "$last_suggestion" ]]; then
    return
  fi

  local suggested_tool
  suggested_tool=$(echo "$last_suggestion" | jq -r '.tool // empty')
  local suggested_agent
  suggested_agent=$(echo "$last_suggestion" | jq -r '.agent // empty')

  if [[ -z "$suggested_tool" ]]; then
    return
  fi

  # Determine outcome: if user immediately reads again, they declined
  if [[ "$current_tool" == "$suggested_tool" ]]; then
    log_suggestion_declined "$suggested_tool" "$suggested_agent"
  else
    log_suggestion_accepted "$suggested_tool" "$suggested_agent"
  fi

  # Clear last_suggestion after logging outcome
  local temp_file="${state_file}.tmp.$$"
  jq 'del(.session.last_suggestion)' "$state_file" > "$temp_file" && mv "$temp_file" "$state_file"
}

# Log that a delegation suggestion was accepted (user switched tools)
log_suggestion_accepted() {
  _log_compliance "advisory_accepted" "$1" "$2" "accepted"
}

# Log that a delegation suggestion was declined (user continued with same tool)
log_suggestion_declined() {
  _log_compliance "advisory_declined" "$1" "$2" "declined"
}

# Increment session-scoped read counter and echo new value
increment_read_counter() {
  local counter_file="${CLAUDE_PROJECT_DIR:-.}/.devsquad/read_count"
  mkdir -p "$(dirname "$counter_file")"

  local current_count
  current_count=$(cat "$counter_file" 2>/dev/null || echo "0")
  local new_count=$((current_count + 1))

  local temp_file="${counter_file}.tmp.$$"
  echo "$new_count" > "$temp_file"
  mv "$temp_file" "$counter_file"
  echo "$new_count"
}

# Get current read count
get_read_count() {
  cat "${CLAUDE_PROJECT_DIR:-.}/.devsquad/read_count" 2>/dev/null || echo "0"
}

# Check if agent CLI is available
# Takes an agent name (e.g., "gemini-reader", "codex-drafter") and returns 0 if CLI exists, 1 if not
check_agent_cli_available() {
  local agent="$1"

  # Extract CLI name from agent prefix
  case "$agent" in
    gemini*) command -v "gemini" &>/dev/null ;;
    codex*)  command -v "codex" &>/dev/null ;;
    *)       return 1 ;;
  esac
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
  _log_compliance "strict_degraded" "$1" "$2" "$3"
}

# Estimate token savings for delegating a file read to Gemini
# Uses file size heuristic: ~4 bytes per token
estimate_token_savings() {
  local file_path="$1"
  local file_size
  file_size=$(stat -f%z "$file_path" 2>/dev/null || stat -c%s "$file_path" 2>/dev/null || echo "0")

  if [[ "$file_size" -eq 0 ]]; then
    echo "~5-20K tokens"
    return
  fi

  local estimated_tokens=$((file_size / 4))
  if [[ "$estimated_tokens" -ge 1000000 ]]; then
    echo "~$((estimated_tokens / 1000000))M tokens"
  elif [[ "$estimated_tokens" -ge 1000 ]]; then
    echo "~$((estimated_tokens / 1000))K tokens"
  else
    echo "~${estimated_tokens} tokens"
  fi
}

# Estimate cumulative savings for all reads above threshold
estimate_session_savings() {
  local threshold="${1:-3}"
  local count
  count=$(cat "${CLAUDE_PROJECT_DIR:-.}/.devsquad/read_count" 2>/dev/null || echo "0")
  local excess=$((count - threshold))

  if [[ "$excess" -le 0 ]]; then
    echo "~5-20K tokens"
  else
    echo "~$((excess * 8))K tokens"
  fi
}

# Get suggestion acceptance metrics from compliance log
get_suggestion_metrics() {
  local project_dir="${CLAUDE_PROJECT_DIR:-.}"
  local log_file="${project_dir}/.devsquad/logs/compliance.log"

  if [[ ! -f "$log_file" ]]; then
    echo '{"suggested": 0, "accepted": 0, "declined": 0, "acceptance_rate": "N/A"}'
    return
  fi

  local suggested accepted declined
  suggested=$(grep -c "advisory_suggested" "$log_file" 2>/dev/null || echo "0")
  accepted=$(grep -c "advisory_accepted" "$log_file" 2>/dev/null || echo "0")
  declined=$(grep -c "advisory_declined" "$log_file" 2>/dev/null || echo "0")

  local rate="N/A"
  local total=$((accepted + declined))
  if [[ "$total" -gt 0 ]]; then
    rate="$((accepted * 100 / total))%"
  fi

  echo "{\"suggested\": ${suggested}, \"accepted\": ${accepted}, \"declined\": ${declined}, \"acceptance_rate\": \"${rate}\"}"
}
