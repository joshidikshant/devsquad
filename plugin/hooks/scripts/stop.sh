#!/usr/bin/env bash
set -euo pipefail

# Hook depth guard -- skip in agent subshells
if [[ "${DEVSQUAD_HOOK_DEPTH:-0}" -ge 1 ]]; then
  exit 0
fi

# Resolve paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PLUGIN_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

source "${PLUGIN_ROOT}/lib/state.sh"

STATE_DIR="${CLAUDE_PROJECT_DIR:-.}/.devsquad"

# Read hook input from stdin
INPUT=$(cat)

# === LOOP GUARD 1: Official stop_hook_active field ===
# If Claude Code says this is already a continuation from a previous stop hook,
# allow the stop immediately to prevent infinite loops.
STOP_HOOK_ACTIVE="false"
if command -v jq &>/dev/null; then
  STOP_HOOK_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // "false"')
else
  # Fallback: grep for the field
  if echo "$INPUT" | grep -q '"stop_hook_active"[[:space:]]*:[[:space:]]*true'; then
    STOP_HOOK_ACTIVE="true"
  fi
fi

if [[ "$STOP_HOOK_ACTIVE" == "true" ]]; then
  # Already continuing from a previous stop hook -- MUST allow stop
  rm -f "${STATE_DIR}/stop_retry_count" 2>/dev/null || true
  exit 0
fi

# === LOOP GUARD 2: Session-scoped retry counter ===
# Even if stop_hook_active is false, limit total retries to prevent
# tight loops where hook logic always blocks.
COUNTER_FILE="${STATE_DIR}/stop_retry_count"
RETRY_COUNT=0
MAX_RETRIES=3

if [[ -f "$COUNTER_FILE" ]]; then
  RETRY_COUNT=$(cat "$COUNTER_FILE" 2>/dev/null || echo "0")
  # Validate it's a number
  if ! [[ "$RETRY_COUNT" =~ ^[0-9]+$ ]]; then
    RETRY_COUNT=0
  fi
fi

if [[ "$RETRY_COUNT" -ge "$MAX_RETRIES" ]]; then
  # Hit retry limit -- MUST allow stop and reset counter
  rm -f "$COUNTER_FILE" 2>/dev/null || true
  exit 0
fi

# Increment counter atomically
TEMP_FILE="${COUNTER_FILE}.tmp.$$"
echo $((RETRY_COUNT + 1)) > "$TEMP_FILE"
mv "$TEMP_FILE" "$COUNTER_FILE"

# === STOP DECISION LOGIC ===
# Currently: allow all stops (no quality gate blocking).
# This hook exists primarily for loop prevention infrastructure.
# Future phases can add quality gates here (e.g., check for incomplete tasks).
#
# To block a stop, output JSON:
# { "decision": "block", "reason": "Explanation..." }
#
# To allow a stop, exit 0 (or output nothing).

# Allow stop and reset counter
rm -f "$COUNTER_FILE" 2>/dev/null || true
exit 0
