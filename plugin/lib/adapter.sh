#!/usr/bin/env bash
# lib/adapter.sh -- Shared CLI adapter core for DevSquad wrappers (D4).
# Sourced by gemini-/codex-/grok-/claude-wrapper.sh. Do not execute directly.
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
# shellcheck source=/dev/null
source "${_ADAPTER_LIB_DIR}/../core/adapters/classification-policy.conf"

# Terminate a bounded subprocess tree without requiring GNU timeout, setsid,
# or job-control process groups (all absent on a stock macOS Bash 3.2 host).
# Descendants are collected before the parent so an exiting parent cannot
# orphan children between discovery and signalling.
_adapter_snapshot_tree() {
  local root_pid="$1" child
  for child in $(pgrep -P "$root_pid" 2>/dev/null || true); do
    _adapter_snapshot_tree "$child"
  done
  printf '%s\n' "$root_pid"
}

_adapter_signal_snapshot() {
  local snapshot_file="$1" signal="${2:-TERM}" pid
  [[ -f "$snapshot_file" ]] || return 0
  while IFS= read -r pid; do
    [[ -n "$pid" ]] && kill -"$signal" "$pid" 2>/dev/null || true
  done < "$snapshot_file"
}

# Bash's own job table tracks our directly-owned children without spawning
# ps/tr for every poll. Timer cancellation below never signals a numeric PID.
_adapter_job_running() {
  local pid="$1" running
  running=$(jobs -pr)
  case $'\n'"$running"$'\n' in
    *$'\n'"$pid"$'\n'*) return 0 ;;
    *) return 1 ;;
  esac
}

_adapter_stop_timer() {
  local timer_pid="${1:-}" control_file="${2:-}"
  [[ -n "$timer_pid" ]] || return 0
  # Duplex open cannot block if the timer has already exited. Keep it open
  # through wait so an early cancellation remains buffered until the timer
  # opens its reader. The command-local descriptor restores the caller's FD9.
  # No numeric PID signal is ever sent to a timer that may have exited/reaped.
  {
    printf 'cancel\n' >&9
    wait "$timer_pid" 2>/dev/null || true
  } 9<>"$control_file"
}

_adapter_cleanup_timer() {
  _adapter_stop_timer "${timer_pid:-}" "${timer_control_file:-}"
  if [[ -n "${timer_dir:-}" ]]; then
    rm -f "$timer_dir/control" "$timer_dir/deadline" "$timer_dir/processes"
    rmdir "$timer_dir" 2>/dev/null || true
  fi
}

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
  if [[ -n "${DEVSQUAD_TEST_ADAPTER_EXECUTABLE:-}" ]]; then
    case "$DEVSQUAD_TEST_ADAPTER_EXECUTABLE" in
      /*) [[ -x "$DEVSQUAD_TEST_ADAPTER_EXECUTABLE" ]] && cli="$DEVSQUAD_TEST_ADAPTER_EXECUTABLE" ;;
      *) _adapter_fail "CLI_ERROR: test adapter executable must be absolute"; return 1 ;;
    esac
  fi
  if [[ -z "$cli" ]]; then
    _adapter_fail "CLI_ERROR: ${ADAPTER_MISSING_MSG}"
    return 1
  fi

  local model
  model=$(_adapter_resolve_model "$ADAPTER_PREF_MODEL_KEY")
  ADAPTER_ARGS=()
  _adapter_build_args "$final_prompt" "$model" "$timeout_secs"

  local timeout_cmd=""
  if [[ "${DEVSQUAD_FORCE_PORTABLE_TIMEOUT:-0}" != "1" ]] && command -v timeout &>/dev/null; then timeout_cmd="timeout"
  elif [[ "${DEVSQUAD_FORCE_PORTABLE_TIMEOUT:-0}" != "1" ]] && command -v gtimeout &>/dev/null; then timeout_cmd="gtimeout"
  fi

  local stderr_file stdout_file
  stderr_file=$(mktemp)
  stdout_file=$(mktemp)
  local timer_pid="" timer_dir="" timer_control_file=""
  local previous_exit_trap
  previous_exit_trap=$(builtin trap -p EXIT)
  trap '_adapter_cleanup_timer; rm -f "${stderr_file:-}" "${stdout_file:-}"' EXIT

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
    timer_dir=$(mktemp -d)
    timer_control_file="$timer_dir/control"
    local timer_deadline_file="$timer_dir/deadline"
    mkfifo -m 600 "$timer_control_file"
    # Bash read's real deadline is independent of polling/inspection cost.
    # Its private FIFO is cancellation authority: no timer-PID signalling,
    # orphan sleep, inherited ignored TERM, or retained capture descriptors.
    (
      trap - EXIT
      local timer_message="" timer_status=0
      if IFS= read -r -t "$timeout_secs" timer_message <>"$timer_control_file"; then
        [[ "$timer_message" == "cancel" ]] || printf 'error\n' >"$timer_deadline_file"
      else
        timer_status=$?
        if [[ "$timer_status" -eq 1 || "$timer_status" -gt 128 ]]; then
          printf 'timeout\n' >"$timer_deadline_file"
        else
          printf 'error\n' >"$timer_deadline_file"
        fi
      fi
    ) </dev/null >/dev/null 2>&1 &
    timer_pid=$!
    if [[ -n "${ADAPTER_STDIN_FILE:-}" ]]; then
      "$cli" "${ADAPTER_ARGS[@]}" <"$ADAPTER_STDIN_FILE" >"$stdout_file" 2>"$stderr_file" &
    else
      "$cli" "${ADAPTER_ARGS[@]}" >"$stdout_file" 2>"$stderr_file" &
    fi
    local cli_pid=$!
    local process_snapshot="$timer_dir/processes" timed_out="false" monitor_failed="false"
    while _adapter_job_running "$cli_pid"; do
      # Observe completion, not an in-progress marker write. The completed
      # timer has published either its actual deadline or a monitor failure.
      if ! _adapter_job_running "$timer_pid"; then
        if [[ "$(cat "$timer_deadline_file" 2>/dev/null || true)" == "timeout" ]]; then
          timed_out="true"
        else
          monitor_failed="true"
        fi
        _adapter_snapshot_tree "$cli_pid" > "$process_snapshot"
        _adapter_signal_snapshot "$process_snapshot" TERM
        sleep 0.1
        _adapter_signal_snapshot "$process_snapshot" KILL
        break
      fi
      sleep 0.05
    done
    _adapter_stop_timer "$timer_pid" "$timer_control_file"
    timer_pid=""
    if wait "$cli_pid"; then
      exit_code=0
    else
      exit_code=$?
    fi
    if [[ "$timed_out" == "true" ]]; then
      _adapter_signal_snapshot "$process_snapshot" KILL
      exit_code=124
    elif [[ "$monitor_failed" == "true" ]]; then
      exit_code=125
    fi
    _adapter_cleanup_timer
    timer_dir=""
    timer_control_file=""
  fi

  local stdout stderr_content
  stdout=$(cat "$stdout_file" 2>/dev/null)
  stderr_content=$(cat "$stderr_file" 2>/dev/null)
  rm -f "$stderr_file" "$stdout_file"
  # Cleanup uses invocation locals only while they are live. Restore the
  # caller's shell-generated trap before any classification/return unwinds
  # that scope, so same-named caller globals can never become cleanup targets.
  builtin trap - EXIT
  if [[ -n "$previous_exit_trap" ]]; then eval "$previous_exit_trap"; fi

  # CLI-specific auth signal (may appear on stdout with exit 0, e.g. grok's
  # sign-in banner) — checked before the success path
  if [[ -n "${ADAPTER_EXTRA_AUTH_RE:-}" ]] && printf '%s\n%s' "$stdout" "$stderr_content" | grep -qiE "$ADAPTER_EXTRA_AUTH_RE"; then
    _adapter_fail "AUTH_ERROR: ${agent} CLI is not authenticated. ${ADAPTER_AUTH_HINT}"
  elif [[ $exit_code -eq 0 ]]; then
    if [[ -z "$stdout" ]]; then
      _adapter_fail "CLI_ERROR: ${agent} returned an empty response. ${ADAPTER_FALLBACK}"
      return 1
    fi
    update_agent_stats "$state_dir" "$agent" "true"
    record_usage "$agent" "$chars_in" "${#stdout}"
    log_contract_check "$agent" "$final_prompt" "$stdout" || true
    echo "$stdout"
    return 0
  elif [[ $exit_code -eq 124 ]]; then
    _adapter_fail "TIMEOUT: ${agent} did not respond within ${timeout_secs}s. ${ADAPTER_FALLBACK}"
  elif echo "$stderr_content" | grep -qiE "$DEVSQUAD_AUTH_ERROR_PATTERN"; then
    _adapter_fail "AUTH_ERROR: ${agent} CLI authentication failed. ${ADAPTER_AUTH_HINT}"
  elif echo "$stderr_content" | grep -qiE "$DEVSQUAD_RATE_LIMIT_PATTERN"; then
    record_rate_limit "$state_dir" "$agent"
    _adapter_fail "RATE_LIMITED: ${agent} hit a rate limit. 2-minute cooldown started. ${ADAPTER_FALLBACK}"
  else
    local stderr_snippet
    stderr_snippet=$(echo "$stderr_content" | head -c 200)
    _adapter_fail "CLI_ERROR: ${agent} failed (exit ${exit_code}). stderr: ${stderr_snippet}. ${ADAPTER_FALLBACK}"
  fi
}
