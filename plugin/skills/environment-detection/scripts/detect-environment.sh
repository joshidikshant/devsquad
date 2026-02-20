#!/usr/bin/env bash
# skills/environment-detection/scripts/detect-environment.sh
# Detects installed AI CLIs and outputs a structured JSON report to stdout.
# All warnings/debug messages go to stderr. Only JSON goes to stdout.
set -euo pipefail

# --- Resolve plugin root ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Accept --plugin-root argument
PLUGIN_ROOT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --plugin-root)
      PLUGIN_ROOT="$2"
      shift 2
      ;;
    --plugin-root=*)
      PLUGIN_ROOT="${1#*=}"
      shift
      ;;
    *)
      shift
      ;;
  esac
done

# Fallback chain: argument -> env var -> resolve from script location
if [[ -z "$PLUGIN_ROOT" ]]; then
  PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-}"
fi
if [[ -z "$PLUGIN_ROOT" ]]; then
  # Script is at skills/environment-detection/scripts/ -> go up 3 levels
  PLUGIN_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
fi

# --- Source CLI detection library ---
CLI_DETECT_LIB="$PLUGIN_ROOT/lib/cli-detect.sh"
if [[ ! -f "$CLI_DETECT_LIB" ]]; then
  echo "Warning: cli-detect.sh not found at $CLI_DETECT_LIB" >&2
  # Define minimal fallbacks
  detect_cli() { command -v "$1" &>/dev/null && echo "true" || echo "false"; }
  detect_cli_path() { command -v "$1" 2>/dev/null || echo ""; }
  check_jq() { command -v jq &>/dev/null && echo "true" || echo "false"; }
else
  # shellcheck source=../../../lib/cli-detect.sh
  source "$CLI_DETECT_LIB"
fi

# --- Detect CLIs ---
gemini_avail=$(detect_cli "gemini")
codex_avail=$(detect_cli "codex")
claude_avail=$(detect_cli "claude")

gemini_path=$(detect_cli_path "gemini")
codex_path=$(detect_cli_path "codex")
claude_path=$(detect_cli_path "claude")

# --- Version detection (with timeout, graceful failure) ---
get_version() {
  local timeout_cmd=""
  if command -v gtimeout &>/dev/null; then timeout_cmd="gtimeout 5"
  elif command -v timeout &>/dev/null; then timeout_cmd="timeout 5"
  fi
  $timeout_cmd "$1" --version 2>/dev/null | head -1 || true
}

gemini_version="" codex_version="" claude_version=""
[[ "$gemini_avail" == "true" ]] && gemini_version=$(get_version "gemini")
[[ "$codex_avail" == "true" ]] && codex_version=$(get_version "codex")
[[ "$claude_avail" == "true" ]] && claude_version=$(get_version "claude")

# --- Check dependencies ---
jq_avail=$(check_jq)

# --- Build summary ---
available_agents=""
missing_agents=""

for cli_name in gemini codex claude; do
  avail_var="${cli_name}_avail"
  if [[ "${!avail_var}" == "true" ]]; then
    available_agents="${available_agents:+${available_agents}, }\"${cli_name}\""
  else
    missing_agents="${missing_agents:+${missing_agents}, }\"${cli_name}\""
  fi
done

can_delegate="false"
if [[ "$gemini_avail" == "true" || "$codex_avail" == "true" ]]; then
  can_delegate="true"
fi

degraded="false"
if [[ -n "$missing_agents" ]]; then
  degraded="true"
fi

available_json="[${available_agents}]"
missing_json="[${missing_agents}]"

# --- Timestamp ---
timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# --- Output JSON to stdout ---
cat <<ENDJSON
{
  "timestamp": "${timestamp}",
  "clis": {
    "gemini": {"available": ${gemini_avail}, "path": "${gemini_path}", "version": "${gemini_version}"},
    "codex": {"available": ${codex_avail}, "path": "${codex_path}", "version": "${codex_version}"},
    "claude": {"available": ${claude_avail}, "path": "${claude_path}", "version": "${claude_version}"}
  },
  "dependencies": {
    "jq": {"available": ${jq_avail}}
  },
  "summary": {
    "available_agents": ${available_json},
    "missing_agents": ${missing_json},
    "can_delegate": ${can_delegate},
    "degraded": ${degraded}
  }
}
ENDJSON
