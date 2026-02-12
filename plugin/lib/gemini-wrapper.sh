#!/usr/bin/env bash
# lib/gemini-wrapper.sh -- Shared Gemini CLI invocation library
# Sourced by Gemini agent system prompts. Do not execute directly.
#
# Provides:
# - invoke_gemini(prompt, word_limit, timeout_secs): Execute Gemini CLI with auto word-bound appending, timeout, error handling
# - invoke_gemini_with_files(files_arg, prompt, word_limit, timeout_secs): Convenience wrapper for @file patterns

# Cache for state directory resolution
_STATE_DIR_CACHE=""

# Resolve state directory (cached)
_resolve_state_dir() {
  if [[ -n "$_STATE_DIR_CACHE" ]]; then
    echo "$_STATE_DIR_CACHE"
    return
  fi

  local project_dir="${CLAUDE_PROJECT_DIR:-.}"
  _STATE_DIR_CACHE="${project_dir}/.devsquad"
  echo "$_STATE_DIR_CACHE"
}

# Expand @dir/ references to individual @file references
# Usage: expand_dir_refs "@src/auth/ @src/models/user.ts @lib/"
# Directories are expanded recursively for common extensions (.ts, .js, .sh, .py, .go, .rs, .md, .json)
# Files and non-existent paths are kept as-is
expand_dir_refs() {
  local input="$1"
  local result=""

  for token in $input; do
    if [[ "$token" == @* ]]; then
      local path="${token#@}"
      if [[ -d "$path" ]]; then
        # Expand directory to individual @file references
        local files
        files=$(find "$path" -type f \( \
          -name "*.ts" -o -name "*.js" -o -name "*.sh" -o -name "*.py" \
          -o -name "*.go" -o -name "*.rs" -o -name "*.md" -o -name "*.json" \
        \) 2>/dev/null | sort)
        if [[ -n "$files" ]]; then
          while IFS= read -r f; do
            result="${result} @${f}"
          done <<< "$files"
        else
          # No matching files, keep original token
          result="${result} ${token}"
        fi
      else
        # File or non-existent path -- keep as-is
        result="${result} ${token}"
      fi
    else
      # Not an @ token -- keep as-is
      result="${result} ${token}"
    fi
  done

  # Trim leading space
  echo "${result# }"
}

# Main Gemini invocation function
# Usage: invoke_gemini "prompt" word_limit timeout_secs
# word_limit: Auto-appends ". Under X words." if not already present. Default: 300. Use 0 to disable.
# timeout_secs: Timeout for gemini CLI. Default: 60.
invoke_gemini() {
  local prompt="$1"
  local caller_limit="$2"
  local timeout_secs="${3:-60}"

  # Resolve word_limit: caller override > config > hardcoded default
  local word_limit=""
  if [[ -n "$caller_limit" ]]; then
    word_limit="$caller_limit"
  else
    local project_dir="${CLAUDE_PROJECT_DIR:-.}"
    local config_file="${project_dir}/.devsquad/config.json"
    if command -v jq &>/dev/null && [[ -f "$config_file" ]]; then
      word_limit=$(jq -r '.preferences.gemini_word_limit // empty' "$config_file" 2>/dev/null)
    fi
    word_limit="${word_limit:-300}"
  fi

  # Prevent hook recursion
  export DEVSQUAD_HOOK_DEPTH=1

  # Source state library
  local lib_dir
  lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  # shellcheck source=lib/state.sh
  source "${lib_dir}/state.sh"
  # shellcheck source=lib/usage.sh
  source "${lib_dir}/usage.sh"

  local state_dir
  state_dir=$(_resolve_state_dir)

  # Check rate limit first
  local in_cooldown
  in_cooldown=$(check_rate_limit "$state_dir" "gemini")
  if [[ "$in_cooldown" == "true" ]]; then
    local cooldown_file="${state_dir}/cooldown_gemini"
    local cooldown_until
    cooldown_until=$(cat "$cooldown_file" 2>/dev/null || echo "0")
    local cooldown_date
    if date -r "$cooldown_until" &>/dev/null; then
      cooldown_date=$(date -r "$cooldown_until" +"%Y-%m-%d %H:%M:%S")
    else
      cooldown_date=$(date -d "@${cooldown_until}" +"%Y-%m-%d %H:%M:%S" 2>/dev/null || echo "unknown")
    fi
    echo "RATE_LIMITED: Gemini is in cooldown until ${cooldown_date}. Use @codex-developer for code, @codex-tester for tests, or handle synthesis yourself." >&2
    return 1
  fi

  # Auto-append word bound if not present
  local final_prompt="$prompt"
  if [[ "$word_limit" -gt 0 ]]; then
    # Check if prompt already has word bound (case-insensitive)
    if ! echo "$prompt" | grep -iE 'under [0-9]+ words|[0-9]+ words max' &>/dev/null; then
      final_prompt="${prompt}. Under ${word_limit} words."
    fi
  fi

  # Set up temp file for stderr
  local stderr_file
  stderr_file=$(mktemp)
  trap 'rm -f "$stderr_file"' EXIT

  # Determine timeout command (timeout on Linux, gtimeout on macOS)
  local timeout_cmd="timeout"
  if ! command -v timeout &>/dev/null && command -v gtimeout &>/dev/null; then
    timeout_cmd="gtimeout"
  fi

  # Invoke Gemini CLI
  local stdout
  local exit_code
  if command -v "$timeout_cmd" &>/dev/null; then
    stdout=$("$timeout_cmd" "${timeout_secs}s" gemini -p "$final_prompt" 2>"$stderr_file")
    exit_code=$?
  else
    # No timeout available, run directly
    stdout=$(gemini -p "$final_prompt" 2>"$stderr_file")
    exit_code=$?
  fi

  local stderr_content
  stderr_content=$(cat "$stderr_file" 2>/dev/null)

  # Handle exit codes
  if [[ $exit_code -eq 0 ]]; then
    # Success
    if [[ -z "$stdout" ]]; then
      echo "WARNING: Gemini returned empty response" >&2
    fi
    update_agent_stats "$state_dir" "gemini" "true"
    record_usage "gemini" "${#final_prompt}" "${#stdout}"
    echo "$stdout"
    rm -f "$stderr_file"
    return 0
  elif [[ $exit_code -eq 124 ]]; then
    # Timeout
    update_agent_stats "$state_dir" "gemini" "false"
    record_usage "gemini" "${#final_prompt}" "0"
    echo "TIMEOUT: Gemini did not respond within ${timeout_secs}s. Try a simpler prompt, use @codex-developer for code, or @codex-tester for tests." >&2
    rm -f "$stderr_file"
    return 1
  elif echo "$stderr_content" | grep -iE 'rate|limit|429' &>/dev/null; then
    # Rate limit hit
    record_rate_limit "$state_dir" "gemini"
    update_agent_stats "$state_dir" "gemini" "false"
    record_usage "gemini" "${#final_prompt}" "0"
    echo "RATE_LIMITED: Gemini hit rate limit. 2-minute cooldown started. Use @codex-developer for code, @codex-tester for tests, or handle synthesis yourself." >&2
    rm -f "$stderr_file"
    return 1
  elif echo "$stderr_content" | grep -iE 'auth|401|403' &>/dev/null; then
    # Auth error
    update_agent_stats "$state_dir" "gemini" "false"
    record_usage "gemini" "${#final_prompt}" "0"
    echo "AUTH_ERROR: Gemini CLI authentication failed. Run 'gemini auth' to re-authenticate." >&2
    rm -f "$stderr_file"
    return 1
  else
    # Other error
    update_agent_stats "$state_dir" "gemini" "false"
    record_usage "gemini" "${#final_prompt}" "0"
    local stderr_snippet
    stderr_snippet=$(echo "$stderr_content" | head -c 200)
    echo "CLI_ERROR: Gemini failed (exit $exit_code). stderr: ${stderr_snippet}" >&2
    rm -f "$stderr_file"
    return 1
  fi
}

# Convenience wrapper for file-based invocations
# Usage: invoke_gemini_with_files "@src/auth/ @src/models/" "Summarize these" 400 90
invoke_gemini_with_files() {
  local files_arg="$1"
  local prompt="$2"
  local word_limit="${3:-}"
  local timeout_secs="${4:-60}"

  # Resolve word_limit from config if not provided
  if [[ -z "$word_limit" ]]; then
    local project_dir="${CLAUDE_PROJECT_DIR:-.}"
    local config_file="${project_dir}/.devsquad/config.json"
    if command -v jq &>/dev/null && [[ -f "$config_file" ]]; then
      word_limit=$(jq -r '.preferences.gemini_word_limit // empty' "$config_file" 2>/dev/null)
    fi
    word_limit="${word_limit:-300}"
  fi

  # Expand @dir/ references to individual @file references
  local expanded_files
  expanded_files=$(expand_dir_refs "$files_arg")

  local combined_prompt="${expanded_files} ${prompt}"
  invoke_gemini "$combined_prompt" "$word_limit" "$timeout_secs"
}
