#!/usr/bin/env bash
# lib/gemini-wrapper.sh -- Shared Gemini CLI invocation library
# Sourced by Gemini agent system prompts. Do not execute directly.
set -euo pipefail

# Load NVM if available so gemini is on PATH in non-interactive subshells
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
# shellcheck source=/dev/null
[ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh" --no-use 2>/dev/null || true
if [ -d "$NVM_DIR/versions/node" ]; then
  for _nvm_node_dir in "$NVM_DIR"/versions/node/*/bin; do
    case ":${PATH}:" in
      *":${_nvm_node_dir}:"*) ;;
      *) export PATH="${_nvm_node_dir}:${PATH}" ;;
    esac
  done
  unset _nvm_node_dir
fi

# Provides:
# - invoke_gemini(prompt, word_limit, timeout_secs): Execute Gemini CLI with auto word-bound appending, timeout, error handling
# - invoke_gemini_with_files(files_arg, prompt, word_limit, timeout_secs): Convenience wrapper for @file patterns

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

  # Source state and usage libraries
  local lib_dir
  lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  source "${lib_dir}/state.sh"
  source "${lib_dir}/usage.sh"

  local state_dir="${CLAUDE_PROJECT_DIR:-.}/.devsquad"

  # Check rate limit first
  if [[ "$(check_rate_limit "$state_dir" "gemini")" == "true" ]]; then
    local cooldown_until
    cooldown_until=$(cat "${state_dir}/cooldown_gemini" 2>/dev/null || echo "0")
    local cooldown_date
    cooldown_date=$(date -r "$cooldown_until" +"%Y-%m-%d %H:%M:%S" 2>/dev/null \
      || date -d "@${cooldown_until}" +"%Y-%m-%d %H:%M:%S" 2>/dev/null \
      || echo "unknown")
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
  local timeout_cmd=""
  if command -v timeout &>/dev/null; then timeout_cmd="timeout"
  elif command -v gtimeout &>/dev/null; then timeout_cmd="gtimeout"
  fi

  # Invoke Gemini CLI
  local stdout exit_code=0
  if [[ -n "$timeout_cmd" ]]; then
    stdout=$("$timeout_cmd" "${timeout_secs}s" gemini -y -p "$final_prompt" -o text 2>"$stderr_file") || exit_code=$?
  else
    stdout=$(gemini -y -p "$final_prompt" -o text 2>"$stderr_file") || exit_code=$?
  fi


  local stderr_content
  stderr_content=$(cat "$stderr_file" 2>/dev/null)

  # Helper: log failure, record stats, and return 1
  _gemini_fail() {
    local msg="$1"
    echo "$msg" >&2
    update_agent_stats "$state_dir" "gemini" "false"
    record_usage "gemini" "${#final_prompt}" "0"
    return 1
  }

  # Handle exit codes (trap handles stderr_file cleanup)
  if [[ $exit_code -eq 0 ]]; then
    if [[ -z "$stdout" ]]; then
      echo "WARNING: Gemini returned empty response" >&2
    fi
    update_agent_stats "$state_dir" "gemini" "true"
    record_usage "gemini" "${#final_prompt}" "${#stdout}"
    echo "$stdout"
    return 0
  elif [[ $exit_code -eq 124 ]]; then
    _gemini_fail "TIMEOUT: Gemini did not respond within ${timeout_secs}s. Try a simpler prompt, use @codex-developer for code, or @codex-tester for tests."
  elif echo "$stderr_content" | grep -iE 'rate|limit|429' &>/dev/null; then
    record_rate_limit "$state_dir" "gemini"
    _gemini_fail "RATE_LIMITED: Gemini hit rate limit. 2-minute cooldown started. Use @codex-developer for code, @codex-tester for tests, or handle synthesis yourself."
  elif echo "$stderr_content" | grep -iE 'auth|401|403' &>/dev/null; then
    _gemini_fail "AUTH_ERROR: Gemini CLI authentication failed. Run 'gemini auth' to re-authenticate."
  else
    local stderr_snippet
    stderr_snippet=$(echo "$stderr_content" | head -c 200)
    _gemini_fail "CLI_ERROR: Gemini failed (exit $exit_code). stderr: ${stderr_snippet}"
  fi
}

# Convenience wrapper for file-based invocations using stdin piping.
# Bypasses Gemini's file sandbox by piping content via stdin instead of @file tokens.
# Usage: invoke_gemini_with_files "@src/auth/ @src/models/user.ts" "Summarize these" 400 90
invoke_gemini_with_files() {
  local files_arg="$1"
  local prompt="$2"
  local word_limit="${3:-}"
  local timeout_secs="${4:-90}"

  # Build stdin content by concatenating file/dir contents
  local file_content=""
  for token in $files_arg; do
    if [[ "$token" == @* ]]; then
      local path="${token#@}"
      if [[ -f "$path" ]]; then
        file_content+="=== ${path} ===\n$(cat "$path")\n\n"
      elif [[ -d "$path" ]]; then
        while IFS= read -r f; do
          file_content+="=== ${f} ===\n$(cat "$f")\n\n"
        done < <(find "$path" -type f \( \
          -name "*.ts" -o -name "*.js" -o -name "*.sh" -o -name "*.py" \
          -o -name "*.go" -o -name "*.rs" -o -name "*.md" -o -name "*.json" \
        \) 2>/dev/null | sort)
      fi
    fi
  done

  # Resolve word limit
  local effective_limit="${word_limit:-}"
  if [[ -z "$effective_limit" ]]; then
    local project_dir="${CLAUDE_PROJECT_DIR:-.}"
    local config_file="${project_dir}/.devsquad/config.json"
    if command -v jq &>/dev/null && [[ -f "$config_file" ]]; then
      effective_limit=$(jq -r '.preferences.gemini_word_limit // empty' "$config_file" 2>/dev/null)
    fi
    effective_limit="${effective_limit:-300}"
  fi

  # Append word limit to prompt if needed
  local final_prompt="$prompt"
  if [[ "${effective_limit}" -gt 0 ]] 2>/dev/null; then
    if ! echo "$prompt" | grep -iE 'under [0-9]+ words|[0-9]+ words max' &>/dev/null; then
      final_prompt="${prompt}. Under ${effective_limit} words."
    fi
  fi

  # Determine timeout command
  local timeout_cmd=""
  if command -v timeout &>/dev/null; then timeout_cmd="timeout"
  elif command -v gtimeout &>/dev/null; then timeout_cmd="gtimeout"
  fi

  # Pipe file content via stdin — bypasses Gemini sandbox file restrictions
  # Pattern confirmed working: { cat file1; echo "---"; cat file2; } | gemini -y -p "prompt" -o text
  local stderr_file
  stderr_file=$(mktemp)
  trap 'rm -f "$stderr_file"' EXIT

  local stdout exit_code=0
  if [[ -n "$file_content" ]]; then
    if [[ -n "$timeout_cmd" ]]; then
      stdout=$(printf '%b' "$file_content" | "$timeout_cmd" "${timeout_secs}s" gemini -y -p "$final_prompt" -o text 2>"$stderr_file") || exit_code=$?
    else
      stdout=$(printf '%b' "$file_content" | gemini -y -p "$final_prompt" -o text 2>"$stderr_file") || exit_code=$?
    fi
  else
    # No files resolved — fall through to plain invoke
    stdout=$(invoke_gemini "$final_prompt" "$effective_limit" "$timeout_secs") || exit_code=$?
  fi

  if [[ $exit_code -ne 0 ]]; then
    local stderr_snippet
    stderr_snippet=$(cat "$stderr_file" 2>/dev/null | head -c 200)
    echo "CLI_ERROR: gemini file invocation failed (exit ${exit_code}). stderr: ${stderr_snippet}" >&2
    return 1
  fi

  echo "$stdout"
}
