#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# generate-skill.sh — DevSquad Code Generation Skill
# Transforms a natural language description into an executable DevSquad skill
# by researching existing patterns (Gemini) and drafting implementation (Codex).
# ---------------------------------------------------------------------------

# Resolve plugin root from script location — do NOT rely on CLAUDE_PLUGIN_ROOT
# (it may be unset when script runs via Bash tool rather than Claude slash command)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PLUGIN_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

# Source required libraries
source "${PLUGIN_ROOT}/lib/state.sh"
source "${PLUGIN_ROOT}/lib/gemini-wrapper.sh"
source "${PLUGIN_ROOT}/lib/codex-wrapper.sh"

# ---------------------------------------------------------------------------
# Argument parsing (while-loop — Phase 2 locked decision, not getopts)
# ---------------------------------------------------------------------------
DESCRIPTION=""
SKILL_NAME=""
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name)
      shift
      SKILL_NAME="${1:-}"
      shift
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --help|-h)
      echo "Usage: generate-skill.sh <description> [--name skill-name] [--dry-run]"
      echo ""
      echo "Arguments:"
      echo "  <description>    Natural language description of the skill to generate (required)"
      echo "  --name NAME      Override auto-derived skill name (optional)"
      echo "  --dry-run        Research and draft without writing files"
      exit 0
      ;;
    *)
      DESCRIPTION="${DESCRIPTION}${DESCRIPTION:+ }${1}"
      shift
      ;;
  esac
done

# Validate required argument
if [[ -z "$DESCRIPTION" ]]; then
  echo "Error: description is required." >&2
  echo "Usage: generate-skill.sh <description> [--name skill-name] [--dry-run]" >&2
  exit 1
fi

# Derive skill name from description if not provided
# Lowercase, spaces to hyphens, strip non-alphanumeric except hyphens
if [[ -z "$SKILL_NAME" ]]; then
  SKILL_NAME="$(echo "$DESCRIPTION" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd 'a-z0-9-' | sed 's/--*/-/g' | sed 's/^-//;s/-$//')"
  # Truncate to 40 chars max
  SKILL_NAME="${SKILL_NAME:0:40}"
fi

echo ""
echo "=== DevSquad Code Generation ==="
echo "Description : ${DESCRIPTION}"
echo "Skill name  : ${SKILL_NAME}"
echo ""

# ---------------------------------------------------------------------------
# Phase 1: Research (Gemini) — implemented in Plan 02
# ---------------------------------------------------------------------------
echo "[1/4] Researching DevSquad patterns with Gemini..."
# STUB — replaced by Plan 02
RESEARCH="[research stub — Plan 02 will implement]"

# ---------------------------------------------------------------------------
# Phase 2: Draft (Codex) — implemented in Plan 02
# ---------------------------------------------------------------------------
echo "[2/4] Drafting skill implementation with Codex..."
# STUB — replaced by Plan 02
DRAFT="[draft stub — Plan 02 will implement]"

# ---------------------------------------------------------------------------
# Phase 3: Review — implemented in Plan 03
# ---------------------------------------------------------------------------
echo "[3/4] Draft ready for review..."
echo ""
echo "=== GENERATED DRAFT ==="
echo "$DRAFT"
echo "========================"
echo ""
# STUB — confirmation and file writing implemented in Plan 03
echo "[dry-run or stub mode — Plan 03 will implement write phase]"
