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
# If this is a continuation from a previous stop hook, allow immediately.
STOP_HOOK_ACTIVE="false"
if command -v jq &>/dev/null; then
  STOP_HOOK_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // "false"')
elif echo "$INPUT" | grep -q '"stop_hook_active"[[:space:]]*:[[:space:]]*true'; then
  STOP_HOOK_ACTIVE="true"
fi

if [[ "$STOP_HOOK_ACTIVE" == "true" ]]; then
  rm -f "${STATE_DIR}/stop_retry_count" 2>/dev/null || true
  exit 0
fi

# === LOOP GUARD 2: Session-scoped retry counter ===
# Limit total retries to prevent tight loops where hook logic always blocks.
COUNTER_FILE="${STATE_DIR}/stop_retry_count"
RETRY_COUNT=$(cat "$COUNTER_FILE" 2>/dev/null || echo "0")
[[ "$RETRY_COUNT" =~ ^[0-9]+$ ]] || RETRY_COUNT=0

if [[ "$RETRY_COUNT" -ge 3 ]]; then
  rm -f "$COUNTER_FILE" 2>/dev/null || true
  exit 0
fi

# Increment counter atomically
echo $((RETRY_COUNT + 1)) > "${COUNTER_FILE}.tmp.$$"
mv "${COUNTER_FILE}.tmp.$$" "$COUNTER_FILE"

# Currently: allow all stops (no quality gate blocking).
# Future phases can add quality gates here.
rm -f "$COUNTER_FILE" 2>/dev/null || true
exit 0
