#!/usr/bin/env bash
# DevSquad composite installer: standalone core first, optional Claude plugin.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd -P)"
REPO_URL="${DEVSQUAD_REPO_URL:-https://github.com/joshidikshant/devsquad.git}"
MARKETPLACE="devsquad-marketplace"
PLUGIN="devsquad@${MARKETPLACE}"
CLAUDE_MODE="auto"
STATUS_MODE=0
JSON_MODE=0
CORE_ARGS=()

usage() {
  cat <<'EOF'
Usage: ./install.sh [options]

Installs the standalone DevSquad runtime first. Claude is not required.
When the Claude CLI is available, the legacy plugin is installed or updated
unless --core-only is supplied.

Composite options:
  --core-only             Install only the standalone runtime
  --with-claude           Require and install/update the Claude plugin

Standalone options are passed to scripts/install-core.sh:
  --source-core PATH      --install-root PATH    --bin-dir PATH
  --python PATH           --with-mcp              --mcp-wheelhouse PATH
  --status                --json
  -h, --help
EOF
}

fail() {
  printf 'devsquad install: %s\n' "$*" >&2
  exit 1
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --core-only) CLAUDE_MODE="skip"; shift ;;
    --with-claude) CLAUDE_MODE="required"; shift ;;
    --source-core|--install-root|--bin-dir|--python|--mcp-wheelhouse)
      [ "$#" -ge 2 ] || fail "$1 requires a value"
      CORE_ARGS+=("$1" "$2"); shift 2 ;;
    --with-mcp)
      CORE_ARGS+=("$1"); shift ;;
    --status)
      STATUS_MODE=1; CORE_ARGS+=("$1"); shift ;;
    --json)
      JSON_MODE=1; CORE_ARGS+=("$1"); shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown option: $1" ;;
  esac
done

if [ "$STATUS_MODE" -eq 1 ] && [ "$CLAUDE_MODE" = "required" ]; then
  fail "--status cannot be combined with --with-claude"
fi
if [ "$STATUS_MODE" -eq 1 ]; then
  CLAUDE_MODE="skip"
fi
if [ "$JSON_MODE" -eq 1 ] && [ "$CLAUDE_MODE" != "skip" ]; then
  fail "--json requires --core-only (or --status); use scripts/install-core.sh for standalone JSON"
fi
if [ "$CLAUDE_MODE" = "required" ] && ! command -v claude >/dev/null 2>&1; then
  fail "--with-claude requested, but the Claude Code CLI is unavailable"
fi

if [ "$JSON_MODE" -eq 1 ]; then
  echo "=== DevSquad standalone runtime ===" >&2
else
  echo "=== DevSquad standalone runtime ==="
fi
if [ "${#CORE_ARGS[@]}" -gt 0 ]; then
  "$SCRIPT_DIR/scripts/install-core.sh" "${CORE_ARGS[@]}"
else
  "$SCRIPT_DIR/scripts/install-core.sh"
fi

if [ "$CLAUDE_MODE" = "skip" ]; then
  if [ "$JSON_MODE" -eq 1 ]; then
    echo "Claude plugin: skipped" >&2
  else
    echo "Claude plugin: skipped"
  fi
  exit 0
fi
if ! command -v claude >/dev/null 2>&1; then
  echo "Claude plugin: skipped (Claude Code CLI not found)"
  echo "Standalone DevSquad is ready; install the plugin later with ./install.sh --with-claude."
  exit 0
fi

echo
echo "=== DevSquad legacy Claude plugin ==="
echo "[1/3] Marketplace"
if claude plugin marketplace list 2>/dev/null | grep -q "$MARKETPLACE"; then
  claude plugin marketplace update "$MARKETPLACE"
else
  claude plugin marketplace add "$REPO_URL"
fi

echo "[2/3] Plugin"
if claude plugin list 2>/dev/null | grep -q "devsquad@"; then
  claude plugin update "$PLUGIN"
else
  claude plugin install "$PLUGIN"
fi

echo "[3/3] Enable"
claude plugin enable "$PLUGIN"

# The plugin's own hooks.json is the single hook registration source. Older
# installers also wrote the same hooks into ~/.claude/settings.json, which can
# double-fire them. This installer deliberately does not add or rewrite global
# hooks without concrete duplicate evidence.
echo "Claude plugin ready. Restart Claude Code, then run /devsquad:setup per project."
