#!/usr/bin/env bash
# lib/adapter.sh -- Shared CLI adapter core for DevSquad wrappers (D4).
# Sourced by gemini-/codex-/grok-wrapper.sh. Do not execute directly.
#
# Contract (enforced by test/test_wrapper_contract.sh):
#   success: response on stdout, exit 0
#   failure: exit 1, stderr prefixed RATE_LIMITED|AUTH_ERROR|TIMEOUT|CLI_ERROR
#   telemetry: usage/<agent>.json record per call, stats in state.json,
#              contracts.log entry on success
#   classification: auth is checked BEFORE rate — a permanent auth failure
#   misread as a rate limit steers agents into infinite retry (Google's
#   Gemini-CLI decommission notice contained 'migrate', which an earlier
#   'rate|limit|429' regex matched)
#
# A wrapper configures these per call, then runs _adapter_invoke:
#   ADAPTER_AGENT           telemetry key (gemini|codex|grok)
#   ADAPTER_PREF_MODEL_KEY  .preferences key holding the global model
#   ADAPTER_AUTH_HINT       how to re-authenticate
#   ADAPTER_FALLBACK        fallback suggestion appended to failures
#   ADAPTER_MISSING_MSG     install guidance when the CLI is absent
#   _adapter_resolve_cli()  echoes the binary to run ("" = not installed)
#   _adapter_build_args()   sets ADAPTER_ARGS given $1=prompt $2=model
#                           $3=timeout_secs (must produce >=1 element:
#                           empty arrays break bash-3.2 under set -u)
# Optional (reset per call — stale values leak across invocations otherwise):
#   ADAPTER_EXTRA_AUTH_RE   extra auth regex checked against stdout+stderr
#                           even on exit 0 (e.g. grok's sign-in banner)
#   ADAPTER_STDIN_FILE      file piped to the CLI's stdin
#   ADAPTER_EXTRA_CHARS_IN  extra chars_in to record (piped content size)
set -euo pipefail

_ADAPTER_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${_ADAPTER_LIB_DIR}/model-catalog.sh"

# Resolve model: agent-specific (agent_models.<DEVSQUAD_AGENT>) >
# global (.preferences.<pref_key>) > "" (CLI default).
# Values may be exact model names OR tiers ("tier:fast" / "tier:frontier"),
# which resolve against the machine-local model catalog at invocation time —
# the anti-churn layer: when providers rotate models, tier pins follow the
# catalog with zero config edits (see lib/model-catalog.sh).
_adapter_resolve_model() {
  local pref_key="$1"
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
    m=$(jq -r --arg k "$pref_key" '.preferences[$k] // empty' "$config_file" 2>/dev/null)
  fi
  if [[ "$m" == tier:* ]]; then
    local cli_key="${pref_key%%_*}"
    m=$(resolve_model_tier "$cli_key" "${m#tier:}" 2>/dev/null || echo "")
  fi
  echo "$m"
}

# _adapter_invoke final_prompt timeout_secs
_adapter_invoke() {
  local final_prompt="$1"
  local timeout_secs="${2:-90}"
  local agent="$ADAPTER_AGENT"

  # Prevent recursive hook firing from CLI subprocesses
  export DEVSQUAD_HOOK_DEPTH=1

  local lib_dir
  lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  source "${lib_dir}/state.sh"
  source "${lib_dir}/usage.sh"

  local state_dir="${CLAUDE_PROJECT_DIR:-.}/.devsquad"
  local chars_in=$(( ${#final_prompt} + ${ADAPTER_EXTRA_CHARS_IN:-0} ))

  # Log failure, record telemetry, return 1 (dynamic scope over locals)
  _adapter_fail() {
    echo "$1" >&2
    update_agent_stats "$state_dir" "$agent" "false"
    record_usage "$agent" "$chars_in" "0"
    return 1
  }

  # Rate-limit cooldown gate
  if [[ "$(check_rate_limit "$state_dir" "$agent")" == "true" ]]; then
    local cooldown_until cooldown_date
    cooldown_until=$(cat "${state_dir}/cooldown_${agent}" 2>/dev/null || echo "0")
    cooldown_date=$(date -r "$cooldown_until" +"%Y-%m-%d %H:%M:%S" 2>/dev/null \
      || date -d "@${cooldown_until}" +"%Y-%m-%d %H:%M:%S" 2>/dev/null \
      || echo "unknown")
    _adapter_fail "RATE_LIMITED: ${agent} is in cooldown until ${cooldown_date}. ${ADAPTER_FALLBACK}"
    return 1
  fi

  local cli
  cli=$(_adapter_resolve_cli)
  if [[ -z "$cli" ]]; then
    _adapter_fail "CLI_ERROR: ${ADAPTER_MISSING_MSG}"
    return 1
  fi

  local model
  model=$(_adapter_resolve_model "$ADAPTER_PREF_MODEL_KEY")
  ADAPTER_ARGS=()
  _adapter_build_args "$final_prompt" "$model" "$timeout_secs"

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
    if [[ -n "${ADAPTER_STDIN_FILE:-}" ]]; then
      "$timeout_cmd" "${timeout_secs}s" "$cli" "${ADAPTER_ARGS[@]}" <"$ADAPTER_STDIN_FILE" >"$stdout_file" 2>"$stderr_file" || exit_code=$?
    else
      "$timeout_cmd" "${timeout_secs}s" "$cli" "${ADAPTER_ARGS[@]}" >"$stdout_file" 2>"$stderr_file" || exit_code=$?
    fi
  else
    # Portable watchdog: every call is bounded even on hosts with no
    # timeout/gtimeout binary (observed live: an unauthenticated CLI
    # waiting on OAuth blocks forever)
    if [[ -n "${ADAPTER_STDIN_FILE:-}" ]]; then
      "$cli" "${ADAPTER_ARGS[@]}" <"$ADAPTER_STDIN_FILE" >"$stdout_file" 2>"$stderr_file" &
    else
      "$cli" "${ADAPTER_ARGS[@]}" >"$stdout_file" 2>"$stderr_file" &
    fi
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

  local stdout stderr_content
  stdout=$(cat "$stdout_file" 2>/dev/null)
  stderr_content=$(cat "$stderr_file" 2>/dev/null)

  # CLI-specific auth signal (may appear on stdout with exit 0, e.g. grok's
  # sign-in banner) — checked before the success path
  if [[ -n "${ADAPTER_EXTRA_AUTH_RE:-}" ]] && printf '%s\n%s' "$stdout" "$stderr_content" | grep -qiE "$ADAPTER_EXTRA_AUTH_RE"; then
    _adapter_fail "AUTH_ERROR: ${agent} CLI is not authenticated. ${ADAPTER_AUTH_HINT}"
  elif [[ $exit_code -eq 0 ]]; then
    if [[ -z "$stdout" ]]; then
      echo "WARNING: ${agent} returned empty response" >&2
    fi
    update_agent_stats "$state_dir" "$agent" "true"
    record_usage "$agent" "$chars_in" "${#stdout}"
    log_contract_check "$agent" "$final_prompt" "$stdout" || true
    echo "$stdout"
    return 0
  elif [[ $exit_code -eq 124 ]]; then
    _adapter_fail "TIMEOUT: ${agent} did not respond within ${timeout_secs}s. ${ADAPTER_FALLBACK}"
  elif echo "$stderr_content" | grep -qiE 'auth|401|403|ineligible|unauthorized'; then
    _adapter_fail "AUTH_ERROR: ${agent} CLI authentication failed. ${ADAPTER_AUTH_HINT}"
  elif echo "$stderr_content" | grep -qiE '429|rate.?limit|quota|resource.?exhausted|too many requests'; then
    record_rate_limit "$state_dir" "$agent"
    _adapter_fail "RATE_LIMITED: ${agent} hit a rate limit. 2-minute cooldown started. ${ADAPTER_FALLBACK}"
  else
    local stderr_snippet
    stderr_snippet=$(echo "$stderr_content" | head -c 200)
    _adapter_fail "CLI_ERROR: ${agent} failed (exit ${exit_code}). stderr: ${stderr_snippet}. ${ADAPTER_FALLBACK}"
  fi
}
