#!/usr/bin/env bash
# lib/grok-wrapper.sh -- Grok Build CLI adapter configuration.
# Sourced by agent system prompts. Do not execute directly.
# Shared invocation core lives in lib/adapter.sh (D4 contract).
set -euo pipefail

# Ensure grok (installed to ~/.grok/bin) is on PATH in non-interactive
# hook/agent subshells where .zshrc is not sourced.
if [ -d "$HOME/.grok/bin" ]; then
  case ":${PATH}:" in
    *":$HOME/.grok/bin:"*) ;;
    *) export PATH="$HOME/.grok/bin:${PATH}" ;;
  esac
fi

_GROK_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${_GROK_LIB_DIR}/adapter.sh"

# Model: agent-specific (agent_models.<DEVSQUAD_AGENT>) > preferences.grok_model
# > grok default (grok-build). List models with `grok models` (requires auth).
_resolve_grok_model() {
  _adapter_resolve_model "grok_model"
}

_grok_configure_adapter() {
  ADAPTER_AGENT="grok"
  ADAPTER_PREF_MODEL_KEY="grok_model"
  ADAPTER_AUTH_HINT="Run 'grok login' once, then retry."
  ADAPTER_FALLBACK="Fallback: @gemini-researcher for research, @codex-developer for code."
  ADAPTER_MISSING_MSG="Grok Build CLI not installed (expected at ~/.grok/bin/grok). See https://grok.com/build"
  # Unauthenticated grok can EXIT 0 with a sign-in banner on stdout, or block
  # on the OAuth device flow (bounded by the adapter watchdog)
  ADAPTER_EXTRA_AUTH_RE='signing in with grok|not authenticated'
  ADAPTER_STDIN_FILE=""
  ADAPTER_EXTRA_CHARS_IN=0

  _adapter_resolve_cli() {
    if command -v grok &>/dev/null; then
      echo "grok"
    else
      echo ""
    fi
  }

  _adapter_build_args() {
    ADAPTER_ARGS=("--always-approve" "-p" "$1" "--output-format" "plain")
    if [[ -n "$2" ]]; then
      ADAPTER_ARGS+=("-m" "$2")
    fi
  }
}

# Main invocation function for Grok Build CLI
# Usage: invoke_grok "prompt" [word_limit] [timeout_secs]
# Returns 0 on success (stdout contains response), 1 on failure.
# Error prefixes: RATE_LIMITED, AUTH_ERROR, TIMEOUT, CLI_ERROR
# Default timeout is deliberately high: grok spins up a full agent session
# per call — measured ~150s single-turn latency for a trivial prompt.
invoke_grok() {
  local prompt="$1"
  local caller_limit="${2:-}"
  local timeout_secs="${3:-240}"

  # Resolve word_limit: caller override > config > 300; append bound if absent
  local word_limit=""
  if [[ -n "$caller_limit" ]]; then
    word_limit="$caller_limit"
  else
    local config_file="${CLAUDE_PROJECT_DIR:-.}/.devsquad/config.json"
    if command -v jq &>/dev/null && [[ -f "$config_file" ]]; then
      word_limit=$(jq -r '.preferences.grok_word_limit // empty' "$config_file" 2>/dev/null)
    fi
    word_limit="${word_limit:-300}"
  fi

  local final_prompt="$prompt"
  if [[ "$word_limit" -gt 0 ]] 2>/dev/null; then
    if ! echo "$prompt" | grep -iE 'under [0-9]+ (words|lines)|[0-9]+ (words|lines) max' &>/dev/null; then
      final_prompt="${prompt}. Under ${word_limit} words."
    fi
  fi

  _grok_configure_adapter
  _adapter_invoke "$final_prompt" "$timeout_secs"
}
