#!/usr/bin/env bash
set -euo pipefail

# Hook depth guard -- skip in agent subshells
if [[ "${DEVSQUAD_HOOK_DEPTH:-0}" -ge 1 ]]; then
  exit 0
fi

# Resolve paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PLUGIN_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

source "${PLUGIN_ROOT}/lib/enforcement.sh"
source "${PLUGIN_ROOT}/lib/state.sh"
source "${PLUGIN_ROOT}/lib/usage.sh"

# Read hook input from stdin (JSON with tool_name and tool_input)
INPUT=$(cat)

# Parse tool name -- jq preferred, grep fallback
if command -v jq &>/dev/null; then
  TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
else
  TOOL_NAME=$(echo "$INPUT" | grep -o '"tool_name"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | cut -d'"' -f4)
fi

# Exit early if tool name empty
if [[ -z "$TOOL_NAME" ]]; then
  exit 0
fi

# Check if previous suggestion was accepted or declined
check_and_log_suggestion_outcome "$TOOL_NAME"

SHOULD_DELEGATE=false
REASON=""
AGENT=""
COMMAND=""
FILE_PATH=""

# Calculate current zone and adjust thresholds
CLAUDE_STATS=$(read_claude_stats)
if command -v jq &>/dev/null; then
  INPUT_TOKENS=$(echo "$CLAUDE_STATS" | jq -r '.input_tokens // 0')
  OUTPUT_TOKENS=$(echo "$CLAUDE_STATS" | jq -r '.output_tokens // 0')
else
  INPUT_TOKENS=$(echo "$CLAUDE_STATS" | grep -o '"input_tokens":[0-9]*' | grep -o '[0-9]*$' || echo "0")
  OUTPUT_TOKENS=$(echo "$CLAUDE_STATS" | grep -o '"output_tokens":[0-9]*' | grep -o '[0-9]*$' || echo "0")
fi

CURRENT_ZONE=$(calculate_zone "$INPUT_TOKENS" "$OUTPUT_TOKENS")

# Update session zone in state
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-.}"
STATE_FILE="${PROJECT_DIR}/.devsquad/state.json"
if command -v jq &>/dev/null; then
  update_state_key "$STATE_FILE" "session.zone" "\"${CURRENT_ZONE}\""
fi

# Zone-adjusted threshold: green=3, yellow/red=1
READ_THRESHOLD=3
if [[ "$CURRENT_ZONE" == "yellow" ]] || [[ "$CURRENT_ZONE" == "red" ]]; then
  READ_THRESHOLD=1
fi

case "$TOOL_NAME" in
  Read)
    # IMPORTANT: Read tool's file_path is always a single string (one call = one file).
    # Track session-scoped counter across multiple Read calls.
    NEW_COUNT=$(increment_read_counter)
    if [[ "$NEW_COUNT" -gt "$READ_THRESHOLD" ]]; then
      SHOULD_DELEGATE=true
      ZONE_PREFIX=""
      if [[ "$CURRENT_ZONE" == "red" ]]; then
        ZONE_PREFIX="RED ZONE (heavy daily token usage). "
      fi
      # Extract file_path for helpful command and savings estimation
      if command -v jq &>/dev/null; then
        FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // "the target files"')
      else
        FILE_PATH="the target files"
      fi
      # Estimate context savings
      FILE_SAVINGS=$(estimate_token_savings "$FILE_PATH")
      SESSION_SAVINGS=$(estimate_session_savings "$READ_THRESHOLD")
      REASON="${ZONE_PREFIX}You have read ${NEW_COUNT} files this session (threshold: ${READ_THRESHOLD}). Delegate bulk reading to @gemini-reader with 1M context to preserve your context window.\n\nEstimated savings: ${FILE_SAVINGS} for this file (${SESSION_SAVINGS} total this session)."
      AGENT="gemini-reader"
      COMMAND="@gemini-reader \"Analyze and summarize: ${FILE_PATH}\""
    fi
    ;;
  Bash)
    # Detect test-related commands in Bash tool_input.command
    bash_command=""
    if command -v jq &>/dev/null; then
      bash_command=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
    else
      bash_command=$(echo "$INPUT" | grep -o '"command"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | cut -d'"' -f4)
    fi

    if is_test_command "$bash_command"; then
      SHOULD_DELEGATE=true
      REASON="Test execution detected. Consider delegating to @codex-tester for test generation and execution, preserving your context window."
      AGENT="codex-tester"
      COMMAND="@codex-tester \"${bash_command}\""
    fi
    ;;
  WebSearch)
    SHOULD_DELEGATE=true
    ZONE_PREFIX=""
    if [[ "$CURRENT_ZONE" == "red" ]]; then
      ZONE_PREFIX="RED ZONE (heavy daily token usage). "
    fi
    REASON="${ZONE_PREFIX}WebSearch consumes Claude's context. Delegate web research to @gemini-researcher with 1M context."
    AGENT="gemini-researcher"
    # Extract query for helpful command
    if command -v jq &>/dev/null; then
      QUERY=$(echo "$INPUT" | jq -r '.tool_input.query // "your research question"')
    else
      QUERY="your research question"
    fi
    COMMAND="@gemini-researcher \"Research: ${QUERY}. Under 500 words.\""
    ;;
esac

# If not delegatable, allow silently
if [[ "$SHOULD_DELEGATE" != "true" ]]; then
  exit 0
fi

# Get enforcement mode from config
MODE=$(get_enforcement_mode)

# Log the delegation suggestion
log_delegation "$TOOL_NAME" "$AGENT" "$MODE"

# === Holdout protocol (T3) ===
# With holdout_mode=true in config, sessions split by session_id hash:
#   even hash -> control: suppress the suggestion (logged identically above
#                and in holdout.log, so both arms have the same records)
#   odd hash  -> treatment: normal advisory/strict behavior
# Compare arms after ~20 tasks with: bash lib/holdout-report.sh
# When holdout_mode is absent or false, behavior is unchanged (L4).
HOLDOUT_ENABLED="false"
CONFIG_FILE="${PROJECT_DIR}/.devsquad/config.json"
if [[ -f "$CONFIG_FILE" ]]; then
  if command -v jq &>/dev/null; then
    HOLDOUT_ENABLED=$(jq -r '.holdout_mode // false' "$CONFIG_FILE" 2>/dev/null)
  elif grep -q '"holdout_mode"[[:space:]]*:[[:space:]]*true' "$CONFIG_FILE"; then
    HOLDOUT_ENABLED="true"
  fi
fi

if [[ "$HOLDOUT_ENABLED" == "true" ]]; then
  if command -v jq &>/dev/null; then
    SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
  else
    SESSION_ID=$(echo "$INPUT" | grep -o '"session_id"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | cut -d'"' -f4)
  fi
  ASSIGNMENT="treatment"
  if [[ -n "$SESSION_ID" ]]; then
    SESSION_HASH=$(printf '%s' "$SESSION_ID" | cksum | cut -d' ' -f1)
    if [[ $((SESSION_HASH % 2)) -eq 0 ]]; then
      ASSIGNMENT="control"
    fi
  fi
  HOLDOUT_LOG_DIR="${PROJECT_DIR}/.devsquad/logs"
  mkdir -p "$HOLDOUT_LOG_DIR"
  echo "$(date -u +"%Y-%m-%dT%H:%M:%SZ") | ${SESSION_ID:-unknown} | ${ASSIGNMENT} | ${TOOL_NAME} | ${AGENT} | zone=${CURRENT_ZONE} | mode=${MODE}" >> "${HOLDOUT_LOG_DIR}/holdout.log"
  if [[ "$ASSIGNMENT" == "control" ]]; then
    exit 0
  fi
fi

# Apply enforcement mode
if command -v jq &>/dev/null; then
  if [[ "$MODE" == "strict" ]]; then
    # Check if agent CLI is available before strict deny
    if ! check_agent_cli_available "$AGENT"; then
      # CLI not available - degrade to advisory mode
      CLI_NAME="${AGENT%%-*}"
      DEGRADED_REASON="STRICT MODE DEGRADED: ${CLI_NAME} CLI not installed. Falling back to advisory. ${REASON}"
      log_degradation "$TOOL_NAME" "$AGENT" "CLI not available"
      jq -n --arg reason "$DEGRADED_REASON" --arg cmd "$COMMAND" '{
        hookSpecificOutput: {
          hookEventName: "PreToolUse",
          permissionDecision: "allow",
          additionalContext: ($reason + "\n\nSuggestion: " + $cmd)
        }
      }'
    else
      # CLI available - proceed with strict deny
      jq -n --arg reason "$REASON" --arg cmd "$COMMAND" '{
        hookSpecificOutput: {
          hookEventName: "PreToolUse",
          permissionDecision: "deny",
          permissionDecisionReason: ($reason + "\n\nRun instead: " + $cmd)
        }
      }'
    fi
  else
    # Advisory: allow but add context
    log_suggestion "$TOOL_NAME" "$AGENT"
    # Record suggestion for acceptance tracking
    record_suggestion "$TOOL_NAME" "$AGENT" "$FILE_PATH"
    jq -n --arg reason "$REASON" --arg cmd "$COMMAND" '{
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "allow",
        additionalContext: ($reason + "\n\nSuggestion: " + $cmd)
      }
    }'
  fi
else
  # No jq fallback -- advisory mode only (cannot safely construct deny JSON without jq)
  log_suggestion "$TOOL_NAME" "$AGENT"
  # Record suggestion for acceptance tracking
  record_suggestion "$TOOL_NAME" "$AGENT" "${FILE_PATH:-}"
  # Escape for JSON manually
  ESCAPED_REASON=$(printf '%s' "$REASON" | sed 's/\\/\\\\/g; s/"/\\"/g')
  ESCAPED_CMD=$(printf '%s' "$COMMAND" | sed 's/\\/\\\\/g; s/"/\\"/g')
  cat <<HOOKJSON
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow",
    "additionalContext": "${ESCAPED_REASON}\\n\\nSuggestion: ${ESCAPED_CMD}"
  }
}
HOOKJSON
fi
