#!/usr/bin/env bash
# lib/codex-wrapper.sh -- Codex CLI wrapper for DevSquad agents
# Sourced by agent system prompts. Do not execute directly.

# Resolve state directory (local helper)
_resolve_state_dir() {
  local project_dir="${CLAUDE_PROJECT_DIR:-.}"
  echo "${project_dir}/.devsquad"
}

# Main invocation function for Codex CLI
# Usage: invoke_codex "prompt" [line_limit] [timeout_secs]
#
# Arguments:
#   prompt        - The prompt to send to Codex CLI
#   line_limit    - Max lines for response (default: 50). Set to 0 to skip bound.
#   timeout_secs  - Timeout in seconds (default: 90)
#
# Returns:
#   0 on success (stdout contains Codex response)
#   1 on rate limit, auth error, timeout, or other failure
#
# Error messages are prefixed with error type for easy parsing:
#   RATE_LIMITED: Agent is in cooldown
#   AUTH_ERROR: Authentication failed
#   TIMEOUT: Codex did not respond in time
#   CLI_ERROR: General CLI error
invoke_codex() {
  local prompt="$1"
  local caller_limit="$2"
  local timeout_secs="${3:-90}"

  # Resolve line_limit: caller override > config > hardcoded default
  local line_limit=""
  if [[ -n "$caller_limit" ]]; then
    line_limit="$caller_limit"
  else
    local project_dir="${CLAUDE_PROJECT_DIR:-.}"
    local config_file="${project_dir}/.devsquad/config.json"
    if command -v jq &>/dev/null && [[ -f "$config_file" ]]; then
      line_limit=$(jq -r '.preferences.codex_line_limit // empty' "$config_file" 2>/dev/null)
    fi
    line_limit="${line_limit:-50}"
  fi

  # Resolve state directory
  local STATE_DIR
  STATE_DIR=$(_resolve_state_dir)

  # Source state.sh for rate limit and stats tracking
  local SCRIPT_DIR
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [[ -f "${SCRIPT_DIR}/state.sh" ]]; then
    source "${SCRIPT_DIR}/state.sh"
  else
    echo "ERROR: lib/state.sh not found" >&2
    return 1
  fi
  if [[ -f "${SCRIPT_DIR}/usage.sh" ]]; then
    source "${SCRIPT_DIR}/usage.sh"
  fi

  # Check rate limit cooldown
  local in_cooldown
  in_cooldown=$(check_rate_limit "$STATE_DIR" "codex")
  if [[ "$in_cooldown" == "true" ]]; then
    echo "RATE_LIMITED: Codex is in cooldown. Fallback: Use @gemini-developer for code, @gemini-tester for tests." >&2
    update_agent_stats "$STATE_DIR" "codex" "false"
    record_usage "codex" "${#prompt}" "0"
    return 1
  fi

  # Auto-append line bound if not already present
  local final_prompt="$prompt"
  if [[ "$line_limit" -gt 0 ]]; then
    # Check if prompt already has line limit language (case-insensitive)
    if ! echo "$prompt" | grep -qiE "(under|max(imum)?|limit(ed to)?) [0-9]+ lines?"; then
      final_prompt="${prompt}. Under ${line_limit} lines."
    fi
  fi

  # Determine timeout command (GNU timeout or macOS gtimeout)
  local TIMEOUT_CMD=""
  if command -v timeout &>/dev/null; then
    TIMEOUT_CMD="timeout"
  elif command -v gtimeout &>/dev/null; then
    TIMEOUT_CMD="gtimeout"
  fi

  # Set up temp files for capturing output
  local stdout_file="${STATE_DIR}/codex_stdout.$$"
  local stderr_file="${STATE_DIR}/codex_stderr.$$"

  # Clean up temp files on exit
  trap "rm -f '$stdout_file' '$stderr_file'" EXIT

  # Set hook depth guard to prevent recursive hook firing
  export DEVSQUAD_HOOK_DEPTH=1

  # Invoke Codex CLI with timeout
  local exit_code=0
  if [[ -n "$TIMEOUT_CMD" ]]; then
    "$TIMEOUT_CMD" "${timeout_secs}s" codex exec "$final_prompt" >"$stdout_file" 2>"$stderr_file" || exit_code=$?
  else
    # No timeout available - invoke directly
    codex exec "$final_prompt" >"$stdout_file" 2>"$stderr_file" || exit_code=$?
  fi

  # Read output files
  local stdout_content=""
  local stderr_content=""
  if [[ -f "$stdout_file" ]]; then
    stdout_content=$(cat "$stdout_file")
  fi
  if [[ -f "$stderr_file" ]]; then
    stderr_content=$(cat "$stderr_file")
  fi

  # Handle exit codes and errors
  if [[ $exit_code -eq 0 ]]; then
    # Success case
    if [[ -z "$stdout_content" ]]; then
      echo "WARNING: Codex returned empty response" >&2
      update_agent_stats "$STATE_DIR" "codex" "true"
      record_usage "codex" "${#final_prompt}" "0"
      return 0
    else
      echo "$stdout_content"
      update_agent_stats "$STATE_DIR" "codex" "true"
      record_usage "codex" "${#final_prompt}" "${#stdout_content}"
      return 0
    fi
  elif [[ $exit_code -eq 124 ]]; then
    # Timeout (GNU timeout) or 143 (killed by signal)
    echo "TIMEOUT: Codex did not respond within ${timeout_secs}s. Try a simpler prompt or use @gemini-developer for code, @gemini-tester for tests." >&2
    update_agent_stats "$STATE_DIR" "codex" "false"
    record_usage "codex" "${#final_prompt}" "0"
    return 1
  else
    # Check stderr for specific error types
    local stderr_lower
    stderr_lower=$(echo "$stderr_content" | tr '[:upper:]' '[:lower:]')

    # Rate limit detection
    if echo "$stderr_lower" | grep -qE "(rate|limit|429)"; then
      echo "RATE_LIMITED: Codex API rate limit hit. Cooldown for 2 minutes. Fallback: Use @gemini-developer for code, @gemini-tester for tests." >&2
      record_rate_limit "$STATE_DIR" "codex"
      update_agent_stats "$STATE_DIR" "codex" "false"
      record_usage "codex" "${#final_prompt}" "0"
      return 1
    fi

    # Auth error detection
    if echo "$stderr_lower" | grep -qE "(auth|401|403|unauthorized)"; then
      echo "AUTH_ERROR: Codex CLI authentication failed. Run 'codex auth' to re-authenticate." >&2
      update_agent_stats "$STATE_DIR" "codex" "false"
      record_usage "codex" "${#final_prompt}" "0"
      return 1
    fi

    # General CLI error
    local stderr_preview
    stderr_preview=$(echo "$stderr_content" | head -c 200)
    echo "CLI_ERROR: Codex failed (exit $exit_code). stderr: ${stderr_preview}. Fallback: Use @gemini-developer for code, @gemini-tester for tests." >&2
    update_agent_stats "$STATE_DIR" "codex" "false"
    record_usage "codex" "${#final_prompt}" "0"
    return 1
  fi
}
