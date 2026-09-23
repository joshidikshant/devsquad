#!/usr/bin/env bash
# lib/claude-wrapper.sh -- Claude Code headless adapter configuration.
# Sourced by agent system prompts. Do not execute directly.
# Shared invocation core lives in lib/adapter.sh (D4 contract).
set -euo pipefail

_CLAUDE_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${_CLAUDE_LIB_DIR}/adapter.sh"

_resolve_claude_model() {
  _adapter_resolve_model "claude_model"
}

_claude_configure_adapter() {
  ADAPTER_AGENT="claude"
  ADAPTER_PREF_MODEL_KEY="claude_model"
  ADAPTER_AUTH_HINT="Run 'claude auth login' through the normal subscription flow, then retry."
  ADAPTER_FALLBACK="Fallback: use a separately qualified DevSquad profile."
  ADAPTER_MISSING_MSG="Claude Code CLI not installed. Install it through the official Claude Code setup."
  ADAPTER_EXTRA_AUTH_RE='not logged in|please run /login|login required'
  ADAPTER_STDIN_FILE=""
  ADAPTER_EXTRA_CHARS_IN=0

  _adapter_resolve_cli() {
    if command -v claude &>/dev/null; then
      echo "claude"
    else
      echo ""
    fi
  }

  _adapter_build_args() {
    ADAPTER_ARGS=(
      "--print"
      "--output-format" "text"
      "--safe-mode"
      "--disable-slash-commands"
      "--no-session-persistence"
      "--strict-mcp-config"
      "--mcp-config" '{"mcpServers":{}}'
      "--no-chrome"
      "--permission-mode" "plan"
      "--tools" "Read,Glob,Grep"
    )
    if [[ -n "$2" ]]; then
      ADAPTER_ARGS+=("--model" "$2")
    fi
    ADAPTER_ARGS+=("$1")
  }
}

# Usage: invoke_claude "prompt" [word_limit] [timeout_secs]
# This compatibility entry point is deliberately read-only. The durable M5
# implementer uses the manifest-driven workspace_write profile instead.
invoke_claude() {
  local prompt="$1"
  local word_limit="${2:-300}"
  local timeout_secs="${3:-180}"
  local final_prompt="$prompt"

  if [[ "$word_limit" -gt 0 ]] 2>/dev/null; then
    if ! echo "$prompt" | grep -qiE 'under [0-9]+ (words|lines)|[0-9]+ (words|lines) max'; then
      final_prompt="${prompt}. Under ${word_limit} words."
    fi
  fi

  _claude_configure_adapter
  _adapter_invoke "$final_prompt" "$timeout_secs"
}
