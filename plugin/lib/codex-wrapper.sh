#!/usr/bin/env bash
# lib/codex-wrapper.sh -- Codex CLI wrapper for DevSquad agents
# Sourced by agent system prompts. Do not execute directly.
set -euo pipefail

# Main invocation function for Codex CLI
# Usage: invoke_codex "prompt" [line_limit] [timeout_secs]
# Returns 0 on success (stdout contains response), 1 on failure.
# Error prefixes: RATE_LIMITED, AUTH_ERROR, TIMEOUT, CLI_ERROR
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

  local STATE_DIR="${CLAUDE_PROJECT_DIR:-.}/.devsquad"

  # Source state and usage libraries
  local SCRIPT_DIR
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  source "${SCRIPT_DIR}/state.sh"
  source "${SCRIPT_DIR}/usage.sh"

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
  if command -v timeout &>/dev/null; then TIMEOUT_CMD="timeout"
  elif command -v gtimeout &>/dev/null; then TIMEOUT_CMD="gtimeout"
  fi

  # Set up temp file for stderr (stdout captured in variable)
  local stderr_file
  stderr_file=$(mktemp)
  trap "rm -f '$stderr_file'" EXIT

  # Prevent recursive hook firing
  export DEVSQUAD_HOOK_DEPTH=1

  # Invoke Codex CLI with timeout
  local stdout_content="" exit_code=0
  if [[ -n "$TIMEOUT_CMD" ]]; then
    stdout_content=$("$TIMEOUT_CMD" "${timeout_secs}s" codex exec "$final_prompt" 2>"$stderr_file") || exit_code=$?
  else
    stdout_content=$(codex exec "$final_prompt" 2>"$stderr_file") || exit_code=$?
  fi

  local stderr_content
  stderr_content=$(cat "$stderr_file" 2>/dev/null)

  # Helper: log failure, record stats, and return 1
  _codex_fail() {
    local msg="$1"
    echo "$msg" >&2
    update_agent_stats "$STATE_DIR" "codex" "false"
    record_usage "codex" "${#final_prompt}" "0"
    return 1
  }

  # Handle exit codes and errors
  if [[ $exit_code -eq 0 ]]; then
    # Success case
    if [[ -z "$stdout_content" ]]; then
      echo "WARNING: Codex returned empty response" >&2
    else
      echo "$stdout_content"
    fi
    update_agent_stats "$STATE_DIR" "codex" "true"
    record_usage "codex" "${#final_prompt}" "${#stdout_content}"
    return 0
  elif [[ $exit_code -eq 124 ]]; then
    _codex_fail "TIMEOUT: Codex did not respond within ${timeout_secs}s. Try a simpler prompt or use @gemini-developer for code, @gemini-tester for tests."
  elif echo "$stderr_content" | grep -qiE 'rate|limit|429'; then
    record_rate_limit "$STATE_DIR" "codex"
    _codex_fail "RATE_LIMITED: Codex API rate limit hit. Cooldown for 2 minutes. Fallback: Use @gemini-developer for code, @gemini-tester for tests."
  elif echo "$stderr_content" | grep -qiE 'auth|401|403|unauthorized'; then
    _codex_fail "AUTH_ERROR: Codex CLI authentication failed. Run 'codex auth' to re-authenticate."
  else
    local stderr_preview
    stderr_preview=$(echo "$stderr_content" | head -c 200)
    _codex_fail "CLI_ERROR: Codex failed (exit $exit_code). stderr: ${stderr_preview}. Fallback: Use @gemini-developer for code, @gemini-tester for tests."
  fi
}
