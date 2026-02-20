#!/usr/bin/env bash
# lib/cli-detect.sh -- Detect availability of AI CLI tools
# Sourced by hooks and other scripts. Do not execute directly.
set -euo pipefail

detect_cli() {
  command -v "$1" &>/dev/null && echo "true" || echo "false"
}

detect_cli_path() {
  local cli_name="$1"
  command -v "$cli_name" 2>/dev/null || echo ""
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
