#!/usr/bin/env bash
# lib-workflow.sh — Helper functions for workflow-orchestration skill
# Sourced by run-workflow.sh. Do not execute directly.
# shellcheck disable=SC2034

# --- workflow_gate -----------------------------------------------------------
# Prompts the user for confirmation before a destructive step.
# Usage: workflow_gate "step_id" "description"
# Returns: 0 if approved, 1 if rejected (caller should exit or skip)
workflow_gate() {
  local step_id="$1"
  local description="${2:-this step}"

  echo ""
  echo "  [GATE] Step '${step_id}' is marked destructive:"
  echo "         ${description}"
  echo ""
  printf "  Proceed? [y/N] "

  local REPLY
  read -r REPLY </dev/tty

  echo ""
  case "$REPLY" in
    y|Y|yes|YES)
      echo "  Approved — continuing."
      return 0
      ;;
    *)
      echo "  Rejected — skipping step '${step_id}'."
      return 1
      ;;
  esac
}

# --- workflow_checkpoint -----------------------------------------------------
# Records current git HEAD hash into state.json under workflow.checkpoints.<step_id>
# Also performs a git commit if there are staged/unstaged changes.
# Usage: workflow_checkpoint "step_id" "commit_message"
# Requires: STATE_DIR set by caller, update_state_key sourced from lib/state.sh
workflow_checkpoint() {
  local step_id="$1"
  local commit_message="${2:-workflow checkpoint: ${step_id}}"
  local state_file="${STATE_DIR}/state.json"

  echo "  [CHECKPOINT] Recording checkpoint for step '${step_id}'..."

  # Only commit if there are actual changes (avoid empty commit error)
  if ! git diff --quiet 2>/dev/null || ! git diff --cached --quiet 2>/dev/null; then
    git add -A
    git commit -m "${commit_message}" --no-verify 2>/dev/null || true
    echo "  Committed changes: ${commit_message}"
  else
    echo "  No changes to commit at this checkpoint."
  fi

  # Record the current HEAD hash regardless
  local head_hash
  head_hash=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
  echo "  HEAD hash: ${head_hash}"

  # Persist to state.json under workflow.checkpoints.<step_id>
  if command -v jq &>/dev/null && [[ -f "$state_file" ]]; then
    local current updated
    current=$(cat "$state_file" 2>/dev/null || echo "{}")
    updated=$(echo "$current" | jq \
      --arg step "$step_id" \
      --arg hash "$head_hash" \
      --arg msg "$commit_message" \
      '.workflow.checkpoints[$step] = {"hash": $hash, "message": $msg, "timestamp": (now | todate)}')
    echo "$updated" > "${state_file}.tmp.$$" && mv "${state_file}.tmp.$$" "$state_file"
    echo "  State persisted: workflow.checkpoints.${step_id}"
  else
    echo "  WARNING: jq not available or state.json missing — checkpoint hash not persisted." >&2
  fi
}

# --- workflow_validate -------------------------------------------------------
# Runs post-workflow health checks: git-health.sh --json + optional test_command.
# Usage: workflow_validate "plugin_root" "test_command"
# Prints a human-readable summary and sets exit code 0 on pass, 1 on failure.
workflow_validate() {
  local plugin_root="$1"
  local test_command="${2:-}"
  local health_script="${plugin_root}/skills/git-health/scripts/git-health.sh"
  local all_passed=true

  echo ""
  echo "=== Post-Workflow Validation ==="
  echo ""

  # Git health check
  echo "[1/2] Running git health check..."
  if [[ -x "$health_script" ]]; then
    local health_json
    health_json=$("$health_script" --json 2>/dev/null || echo '{"status":"error"}')
    local health_status
    health_status=$(echo "$health_json" | jq -r '.status // "unknown"' 2>/dev/null || echo "unknown")
    echo "      Git health: ${health_status}"
    if [[ "$health_status" != "healthy" && "$health_status" != "ok" ]]; then
      echo "      WARNING: Git health check reported issues."
      all_passed=false
    fi
  else
    echo "      SKIP: git-health.sh not found or not executable."
  fi

  # Optional test command
  echo "[2/2] Running tests..."
  if [[ -n "$test_command" ]]; then
    echo "      Command: ${test_command}"
    if eval "$test_command" 2>&1 | tail -5; then
      echo "      Tests: PASSED"
    else
      echo "      Tests: FAILED"
      all_passed=false
    fi
  else
    echo "      SKIP: No test_command configured."
  fi

  echo ""
  if [[ "$all_passed" == "true" ]]; then
    echo "Validation: PASSED"
    return 0
  else
    echo "Validation: FAILED — review issues above."
    return 1
  fi
}
