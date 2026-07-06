#!/usr/bin/env bash
# lib/cli-detect.sh -- Detect availability of AI CLI tools
# Sourced by hooks and other scripts. Do not execute directly.
set -euo pipefail

# Load NVM if available so gemini/codex installed via NVM are on PATH
# in non-interactive hook subshells where .zshrc/.bash_profile are not sourced.
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
# shellcheck source=/dev/null
[ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh" --no-use 2>/dev/null || true
# Also add common nvm node bin paths directly as fallback
if [ -d "$NVM_DIR/versions/node" ]; then
  for _nvm_node_dir in "$NVM_DIR"/versions/node/*/bin; do
    case ":${PATH}:" in
      *":${_nvm_node_dir}:"*) ;;
      *) export PATH="${_nvm_node_dir}:${PATH}" ;;
    esac
  done
  unset _nvm_node_dir
fi

# The "gemini" role is served by the Antigravity CLI (agy) only — Google
# decommissioned the open-source Gemini CLI on 2026-06-18; a `gemini` binary
# on PATH no longer implies a working service, so it must not count.
detect_cli() {
  local cli_name="$1"
  if [[ "$cli_name" == "gemini" ]]; then
    if command -v agy &>/dev/null || command -v antigravity &>/dev/null; then
      echo "true"
    else
      echo "false"
    fi
  else
    command -v "$cli_name" &>/dev/null && echo "true" || echo "false"
  fi
}

detect_cli_path() {
  local cli_name="$1"
  if [[ "$cli_name" == "gemini" ]]; then
    if command -v agy &>/dev/null; then
      command -v agy
    elif command -v antigravity &>/dev/null; then
      command -v antigravity
    else
      echo ""
    fi
  else
    command -v "$cli_name" 2>/dev/null || echo ""
  fi
}

detect_all_clis() {
  local cli
  printf '{\n'
  local first=true
  for cli in gemini codex claude; do
    if [[ "$first" == "true" ]]; then first=false; else printf ',\n'; fi
    printf '  "%s": {"available": %s, "path": "%s"}' "$cli" "$(detect_cli "$cli")" "$(detect_cli_path "$cli")"
  done
  printf '\n}\n'
}

# Check if jq is available (required for JSON operations in hooks)
check_jq() {
  detect_cli "jq"
}
