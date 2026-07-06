#!/usr/bin/env bash
# lib/grok-wrapper.sh -- Grok Build CLI wrapper for DevSquad agents
# Sourced by agent system prompts. Do not execute directly.
set -euo pipefail

# Ensure grok (installed to ~/.grok/bin) is on PATH in non-interactive
# hook/agent subshells where .zshrc is not sourced.
if [ -d "$HOME/.grok/bin" ]; then
  case ":${PATH}:" in
    *":$HOME/.grok/bin:"*) ;;
    *) export PATH="$HOME/.grok/bin:${PATH}" ;;
  esac
fi

# Resolve the external model for this invocation:
#   agent-specific (config agent_models.<DEVSQUAD_AGENT>) >
#   global (preferences.grok_model) > none (grok default: grok-build)
# List models with `grok models` (requires auth).
_resolve_grok_model() {
  local config_file="${CLAUDE_PROJECT_DIR:-.}/.devsquad/config.json"
  if ! command -v jq &>/dev/null || [[ ! -f "$config_file" ]]; then
    echo ""
    return 0
  fi
  local m=""
  if [[ -n "${DEVSQUAD_AGENT:-}" ]]; then
    m=$(jq -r --arg a "$DEVSQUAD_AGENT" '.agent_models[$a] // empty' "$config_file" 2>/dev/null)
  fi
  if [[ -z "$m" ]]; then
    m=$(jq -r '.preferences.grok_model // empty' "$config_file" 2>/dev/null)
  fi
  echo "$m"
}

# Main invocation function for Grok Build CLI
# Usage: invoke_grok "prompt" [word_limit] [timeout_secs]
# Returns 0 on success (stdout contains response), 1 on failure.
# Error prefixes: RATE_LIMITED, AUTH_ERROR, TIMEOUT, CLI_ERROR
invoke_grok() {
  local prompt="$1"
  local caller_limit="${2:-}"
  local timeout_secs="${3:-90}"

  # Resolve word_limit: caller override > config > hardcoded default
  local word_limit=""
  if [[ -n "$caller_limit" ]]; then
    word_limit="$caller_limit"
  else
    local config_file="${CLAUDE_PROJECT_DIR:-.}/.devsquad/config.json"
    if command -v jq &>/dev/null && [[ -f "$config_file" ]]; then
      word_limit=$(jq -r '.preferences.grok_word_limit // empty' "$config_file" 2>/dev/null)
    fi
    word_limit="${word_limit:-300}"
  fi

  # Prevent recursive hook firing
  export DEVSQUAD_HOOK_DEPTH=1

  # Source state and usage libraries
  local lib_dir
  lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  source "${lib_dir}/state.sh"
  source "${lib_dir}/usage.sh"

  local state_dir="${CLAUDE_PROJECT_DIR:-.}/.devsquad"

  # Check rate limit cooldown
  if [[ "$(check_rate_limit "$state_dir" "grok")" == "true" ]]; then
    echo "RATE_LIMITED: Grok is in cooldown. Fallback: @gemini-researcher for research, @codex-developer for code." >&2
    update_agent_stats "$state_dir" "grok" "false"
    record_usage "grok" "${#prompt}" "0"
    return 1
  fi

  # Auto-append word bound if not present
  local final_prompt="$prompt"
  if [[ "$word_limit" -gt 0 ]] 2>/dev/null; then
    if ! echo "$prompt" | grep -iE 'under [0-9]+ (words|lines)|[0-9]+ (words|lines) max' &>/dev/null; then
      final_prompt="${prompt}. Under ${word_limit} words."
    fi
  fi

  if ! command -v grok &>/dev/null; then
    echo "CLI_ERROR: Grok Build CLI not installed (expected at ~/.grok/bin/grok). See https://grok.com/build" >&2
    update_agent_stats "$state_dir" "grok" "false"
    record_usage "grok" "${#final_prompt}" "0"
    return 1
  fi

  local cli_args=("--always-approve" "-p" "$final_prompt" "--output-format" "plain")
  local model_override
  model_override=$(_resolve_grok_model)
  if [[ -n "$model_override" ]]; then
    cli_args+=("-m" "$model_override")
  fi

  # Determine timeout command. NOTE: grok has no native print-timeout flag
  # (unlike agy) — on hosts without timeout/gtimeout, calls are bounded only
  # by grok's own behavior.
  local timeout_cmd=""
  if command -v timeout &>/dev/null; then timeout_cmd="timeout"
  elif command -v gtimeout &>/dev/null; then timeout_cmd="gtimeout"
  fi

  local stderr_file stdout_file
  stderr_file=$(mktemp)
  stdout_file=$(mktemp)
  trap 'rm -f "${stderr_file:-}" "${stdout_file:-}"' EXIT

  local exit_code=0
  if [[ -n "$timeout_cmd" ]]; then
    "$timeout_cmd" "${timeout_secs}s" grok "${cli_args[@]}" >"$stdout_file" 2>"$stderr_file" || exit_code=$?
  else
    # Portable watchdog: this host has no timeout/gtimeout and grok has no
    # native print-timeout flag — an unauthenticated grok waiting on OAuth
    # blocks FOREVER (observed live). Bound it ourselves.
    grok "${cli_args[@]}" >"$stdout_file" 2>"$stderr_file" &
    local cli_pid=$!
    ( sleep "$timeout_secs"; kill "$cli_pid" 2>/dev/null ) &
    local watchdog_pid=$!
    if wait "$cli_pid"; then
      exit_code=0
    else
      exit_code=$?
    fi
    kill "$watchdog_pid" 2>/dev/null || true
    wait "$watchdog_pid" 2>/dev/null || true
    # SIGTERM from the watchdog surfaces as 143 — normalize to timeout's 124
    if [[ $exit_code -eq 143 ]]; then
      exit_code=124
    fi
  fi
  local stdout
  stdout=$(cat "$stdout_file" 2>/dev/null)

  local stderr_content
  stderr_content=$(cat "$stderr_file" 2>/dev/null)

  # Helper: log failure, record stats, and return 1
  _grok_fail() {
    local msg="$1"
    echo "$msg" >&2
    update_agent_stats "$state_dir" "grok" "false"
    record_usage "grok" "${#final_prompt}" "0"
    return 1
  }

  # Unauthenticated grok EXITS 0 and prints a sign-in banner to stdout —
  # this must be caught before the success path, or the wrapper records the
  # banner as a successful response.
  if printf '%s\n%s' "$stdout" "$stderr_content" | grep -qiE 'signing in with grok|not authenticated'; then
    _grok_fail "AUTH_ERROR: Grok Build CLI is not authenticated. Run 'grok login' once, then retry."
  elif [[ $exit_code -eq 0 ]]; then
    if [[ -z "$stdout" ]]; then
      echo "WARNING: Grok returned empty response" >&2
    fi
    update_agent_stats "$state_dir" "grok" "true"
    record_usage "grok" "${#final_prompt}" "${#stdout}"
    log_contract_check "grok" "$final_prompt" "$stdout" || true
    echo "$stdout"
    return 0
  elif [[ $exit_code -eq 124 ]]; then
    _grok_fail "TIMEOUT: Grok did not respond within ${timeout_secs}s. Fallback: @gemini-researcher for research, @codex-developer for code."
  # Auth before rate (see gemini-wrapper.sh — misclassifying a permanent auth
  # failure as a rate limit steers agents into infinite retry)
  elif echo "$stderr_content" | grep -iE 'auth|401|403|unauthorized' &>/dev/null; then
    _grok_fail "AUTH_ERROR: Grok Build CLI authentication failed. Run 'grok login' to re-authenticate."
  elif echo "$stderr_content" | grep -iE '429|rate.?limit|quota|too many requests' &>/dev/null; then
    record_rate_limit "$state_dir" "grok"
    _grok_fail "RATE_LIMITED: Grok hit a rate limit. 2-minute cooldown started. Fallback: @gemini-researcher for research, @codex-developer for code."
  else
    local stderr_snippet
    stderr_snippet=$(echo "$stderr_content" | head -c 200)
    _grok_fail "CLI_ERROR: Grok failed (exit $exit_code). stderr: ${stderr_snippet}"
  fi
}
