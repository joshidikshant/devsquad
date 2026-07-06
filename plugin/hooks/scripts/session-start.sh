#!/usr/bin/env bash
set -euo pipefail

# Prevent recursive hook firing from agent subshells
if [[ "${DEVSQUAD_HOOK_DEPTH:-0}" -ge 1 ]]; then
  echo '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":""}}'
  exit 0
fi

# Resolve paths relative to script location (portable)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PLUGIN_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source libraries
source "${PLUGIN_ROOT}/lib/cli-detect.sh"
source "${PLUGIN_ROOT}/lib/state.sh"
source "${PLUGIN_ROOT}/lib/usage.sh"

# Detect CLIs
GEMINI_AVAIL=$(detect_cli "gemini")
CODEX_AVAIL=$(detect_cli "codex")
GROK_AVAIL=$(detect_cli "grok")
CLAUDE_AVAIL=$(detect_cli "claude")
JQ_AVAIL=$(check_jq)

# Initialize state
STATE_DIR=$(init_state_dir)
ensure_config "${STATE_DIR}"
init_session_state "${STATE_DIR}"

# Reset legacy shared counters; per-session counters (read_count.<sid>,
# suggest_count.<sid>) belong to their own sessions — only expire stale ones
# so concurrent sessions are never clobbered by a new session starting.
rm -f "${STATE_DIR}/read_count" "${STATE_DIR}/suggest_count"
find "${STATE_DIR}" -maxdepth 1 -name "read_count.*" -mtime +1 -delete 2>/dev/null || true
find "${STATE_DIR}" -maxdepth 1 -name "suggest_count.*" -mtime +1 -delete 2>/dev/null || true

# Reset stop hook retry counter for new session
rm -f "${STATE_DIR}/stop_retry_count"

# Create usage session marker
reset_usage_session

# Check for compaction recovery (snapshot.json exists if PreCompact fired)
SNAPSHOT_FILE="${STATE_DIR}/snapshot.json"
COMPACTION_RECOVERY=false
RECOVERED_AGENTS=""
RECOVERED_STATS=""

if [[ -f "$SNAPSHOT_FILE" ]]; then
  COMPACTION_RECOVERY=true
  SNAPSHOT=$(read_state "$SNAPSHOT_FILE")

  # Extract agent names from snapshot
  if [[ "$JQ_AVAIL" == "true" ]]; then
    AGENT_LIST=$(echo "$SNAPSHOT" | jq -r '.agents[]' 2>/dev/null | tr '\n' ', ' | sed 's/,$//' || true)
    if [[ -n "$AGENT_LIST" ]]; then
      RECOVERED_AGENTS="Available agents: ${AGENT_LIST}"
    fi

    # Extract delegation stats from snapshot state
    GEMINI_CALLS=$(echo "$SNAPSHOT" | jq -r '.state.stats.gemini_calls // 0' 2>/dev/null || echo "0")
    CODEX_CALLS=$(echo "$SNAPSHOT" | jq -r '.state.stats.codex_calls // 0' 2>/dev/null || echo "0")
    RECOVERED_STATS="Delegation stats this session: Gemini: ${GEMINI_CALLS} calls, Codex: ${CODEX_CALLS} calls"
  fi

  # Delete snapshot after successful restore (one-shot)
  rm -f "$SNAPSHOT_FILE" || true
fi

# Update state with CLI detection results
if [[ "$JQ_AVAIL" == "true" ]]; then
  CLI_JSON=$(detect_all_clis)
  CURRENT_STATE=$(read_state "${STATE_DIR}/state.json")
  UPDATED_STATE=$(echo "$CURRENT_STATE" | jq --argjson cli "$CLI_JSON" '.cli = $cli')
  write_state "${STATE_DIR}/state.json" "$UPDATED_STATE"
fi

# Detect existing plugins
PLUGINS_INFO=""
PLUGINS_FILE="$HOME/.claude/plugins/installed_plugins.json"
if [[ -f "$PLUGINS_FILE" ]] && [[ "$JQ_AVAIL" == "true" ]]; then
  PLUGIN_NAMES=$(jq -r '.plugins | keys[]' "$PLUGINS_FILE" 2>/dev/null | tr '\n' ', ' | sed 's/,$//' || true)
  if [[ -n "$PLUGIN_NAMES" ]]; then
    PLUGINS_INFO="Discovered plugins: ${PLUGIN_NAMES}"
  fi
fi

# Build availability strings
if [[ "$GEMINI_AVAIL" == "false" ]]; then
  GEMINI_STATUS="NOT INSTALLED (install: brew install --cask antigravity-cli)"
  if command -v gemini &>/dev/null; then
    GEMINI_STATUS="${GEMINI_STATUS} — legacy gemini binary found but Google decommissioned its service June 2026"
  fi
else
  if command -v agy &>/dev/null; then
    GEMINI_STATUS="available (Antigravity CLI: agy)"
  else
    GEMINI_STATUS="available (Antigravity CLI)"
  fi
fi
if [[ "$CODEX_AVAIL" == "false" ]]; then
  CODEX_STATUS="NOT INSTALLED (install: npm i -g @openai/codex)"
else
  CODEX_STATUS="available"
fi
if [[ "$GROK_AVAIL" == "false" ]]; then
  GROK_STATUS="NOT INSTALLED (see https://grok.com/build)"
else
  GROK_STATUS="available"
fi

# Build context
RECOVERY_LINES=""
if [[ "$COMPACTION_RECOVERY" == "true" ]]; then
  RECOVERY_LINES="
Session recovered from compaction."
  [[ -n "$RECOVERED_AGENTS" ]] && RECOVERY_LINES="${RECOVERY_LINES}
- ${RECOVERED_AGENTS}"
  [[ -n "$RECOVERED_STATS" ]] && RECOVERY_LINES="${RECOVERY_LINES}
- ${RECOVERED_STATS}"
fi

CONTEXT="DevSquad is active. You are an Engineering Manager coordinating a squad of AI agents.
${RECOVERY_LINES}
Squad Status:
- Gemini CLI: ${GEMINI_STATUS}
- Codex CLI: ${CODEX_STATUS}
- Grok CLI: ${GROK_STATUS}
${PLUGINS_INFO:+- ${PLUGINS_INFO}}

Commands: /devsquad:setup (onboarding), /devsquad:status (health check), /devsquad:config (preferences)

Delegation principle: Research and bulk reading to Gemini (1M context). Boilerplate drafts to Codex. You handle synthesis and final integration only."

# Output hook response -- must be valid JSON on stdout with nothing else
if [[ "$JQ_AVAIL" == "true" ]]; then
  # Use jq to safely JSON-escape the context string
  cat <<HOOKJSON
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": $(printf '%s' "$CONTEXT" | jq -Rs .)
  }
}
HOOKJSON
else
  # Fallback: manually escape for JSON (newlines, quotes, backslashes)
  ESCAPED_CONTEXT=$(printf '%s' "$CONTEXT" | sed 's/\\/\\\\/g; s/"/\\"/g; s/$/\\n/' | tr -d '\n' | sed 's/\\n$//')
  cat <<HOOKJSON
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "${ESCAPED_CONTEXT}"
  }
}
HOOKJSON
fi
