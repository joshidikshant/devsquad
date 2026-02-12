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
CLAUDE_AVAIL=$(detect_cli "claude")
JQ_AVAIL=$(check_jq)

# Initialize state
STATE_DIR=$(init_state_dir)
ensure_config "${STATE_DIR}"
init_session_state "${STATE_DIR}"

# Reset session-scoped read counter for enforcement hooks
rm -f "${STATE_DIR}/read_count"

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
  GEMINI_STATUS="NOT INSTALLED (install: npm i -g @google/gemini-cli)"
else
  GEMINI_STATUS="available"
fi
if [[ "$CODEX_AVAIL" == "false" ]]; then
  CODEX_STATUS="NOT INSTALLED (install: npm i -g @openai/codex)"
else
  CODEX_STATUS="available"
fi

# Build context
if [[ "$COMPACTION_RECOVERY" == "true" ]]; then
  # Compaction recovery - include restored state
  CONTEXT="DevSquad is active. You are an Engineering Manager coordinating a squad of AI agents.

Session recovered from compaction.

Squad Status:
- Gemini CLI: ${GEMINI_STATUS}
- Codex CLI: ${CODEX_STATUS}
${PLUGINS_INFO:+- ${PLUGINS_INFO}}
${RECOVERED_AGENTS:+- ${RECOVERED_AGENTS}}
${RECOVERED_STATS:+- ${RECOVERED_STATS}}

Commands: /devsquad:setup (onboarding), /devsquad:status (health check), /devsquad:config (preferences)

Delegation principle: Research and bulk reading to Gemini (1M context). Boilerplate drafts to Codex. You handle synthesis and final integration only."
else
  # Fresh session - original context
  CONTEXT="DevSquad is active. You are an Engineering Manager coordinating a squad of AI agents.

Squad Status:
- Gemini CLI: ${GEMINI_STATUS}
- Codex CLI: ${CODEX_STATUS}
${PLUGINS_INFO:+- ${PLUGINS_INFO}}

Commands: /devsquad:setup (onboarding), /devsquad:status (health check), /devsquad:config (preferences)

Delegation principle: Research and bulk reading to Gemini (1M context). Boilerplate drafts to Codex. You handle synthesis and final integration only."
fi

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
