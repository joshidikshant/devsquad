#!/usr/bin/env bash
# lib/codex-wrapper.sh -- Codex CLI adapter configuration.
# Sourced by agent system prompts. Do not execute directly.
# Shared invocation core lives in lib/adapter.sh (D4 contract).
set -euo pipefail

_CODEX_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${_CODEX_LIB_DIR}/adapter.sh"

# Model: agent-specific (agent_models.<DEVSQUAD_AGENT>) > preferences.codex_model
# > codex default (e.g. gpt-5.3-codex via `codex exec -m`)
_resolve_codex_model() {
  _adapter_resolve_model "codex_model"
}

_codex_configure_adapter() {
  ADAPTER_AGENT="codex"
  ADAPTER_PREF_MODEL_KEY="codex_model"
  ADAPTER_AUTH_HINT="Run 'codex auth' to re-authenticate."
  ADAPTER_FALLBACK="Fallback: Use @gemini-developer for code, @gemini-tester for tests."
  ADAPTER_MISSING_MSG="Codex CLI not installed. Install: npm i -g @openai/codex"
  ADAPTER_EXTRA_AUTH_RE=""
  ADAPTER_STDIN_FILE=""
  ADAPTER_EXTRA_CHARS_IN=0

  _adapter_resolve_cli() {
    if command -v codex &>/dev/null; then
      echo "codex"
    else
      echo ""
    fi
  }

  _adapter_build_args() {
    ADAPTER_ARGS=("exec")
    if [[ -n "$2" ]]; then
      ADAPTER_ARGS+=("-m" "$2")
    fi
    ADAPTER_ARGS+=("$1")
  }
}

# Main invocation function for Codex CLI
# Usage: invoke_codex "prompt" [line_limit] [timeout_secs]
# Returns 0 on success (stdout contains response), 1 on failure.
# Error prefixes: RATE_LIMITED, AUTH_ERROR, TIMEOUT, CLI_ERROR
invoke_codex() {
  local prompt="$1"
  local caller_limit="${2:-}"
  local timeout_secs="${3:-90}"

  # Resolve line_limit: caller override > config > 50; append bound if absent
  local line_limit=""
  if [[ -n "$caller_limit" ]]; then
    line_limit="$caller_limit"
  else
    local config_file="${CLAUDE_PROJECT_DIR:-.}/.devsquad/config.json"
    if command -v jq &>/dev/null && [[ -f "$config_file" ]]; then
      line_limit=$(jq -r '.preferences.codex_line_limit // empty' "$config_file" 2>/dev/null)
    fi
    line_limit="${line_limit:-50}"
  fi

  local final_prompt="$prompt"
  if [[ "$line_limit" -gt 0 ]] 2>/dev/null; then
    if ! echo "$prompt" | grep -qiE "(under|max(imum)?|limit(ed to)?) [0-9]+ lines?"; then
      final_prompt="${prompt}. Under ${line_limit} lines."
    fi
  fi

  _codex_configure_adapter
  _adapter_invoke "$final_prompt" "$timeout_secs"
}
