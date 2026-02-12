#!/usr/bin/env bash
# lib/cli-detect.sh -- Detect availability of AI CLI tools
# Sourced by hooks and other scripts. Do not execute directly.

detect_cli() {
  local cli_name="$1"
  local cli_path
  cli_path=$(command -v "$cli_name" 2>/dev/null || true)
  if [[ -n "$cli_path" ]]; then
    echo "true"
  else
    echo "false"
  fi
}

detect_cli_path() {
  local cli_name="$1"
  command -v "$cli_name" 2>/dev/null || echo ""
}

detect_all_clis() {
  # Returns JSON object with availability of all three CLIs
  local gemini_avail codex_avail claude_avail
  local gemini_path codex_path claude_path

  gemini_avail=$(detect_cli "gemini")
  codex_avail=$(detect_cli "codex")
  claude_avail=$(detect_cli "claude")

  gemini_path=$(detect_cli_path "gemini")
  codex_path=$(detect_cli_path "codex")
  claude_path=$(detect_cli_path "claude")

  cat <<ENDJSON
{
  "gemini": {"available": ${gemini_avail}, "path": "${gemini_path}"},
  "codex": {"available": ${codex_avail}, "path": "${codex_path}"},
  "claude": {"available": ${claude_avail}, "path": "${claude_path}"}
}
ENDJSON
}

# Check if jq is available (required for JSON operations in hooks)
check_jq() {
  if ! command -v jq &>/dev/null; then
    echo "false"
  else
    echo "true"
  fi
}
