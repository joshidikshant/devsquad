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
RESEARCH=$(invoke_gemini_with_files \
  "@${PLUGIN_ROOT}/skills/ @${PLUGIN_ROOT}/commands/" \
  "Analyze these DevSquad skill and command files. Extract the exact SKILL.md frontmatter fields (name, description, version) and prose structure (## When to Use, ## Usage, ## Dependencies sections). Extract the shell script conventions: path resolution via BASH_SOURCE, library sourcing order (state.sh first, then wrappers), argument parsing with while-loop. List how the command stub in commands/ references the skill. Under 500 words." \
  500 \
  90)

if [[ $? -ne 0 ]]; then
  echo "Error: Gemini research failed." >&2
  echo "$RESEARCH" >&2
  exit 1
fi

echo "  Research complete (${#RESEARCH} chars)"

# ---------------------------------------------------------------------------
# Phase 2: Draft (Codex) — implemented in Plan 02
# ---------------------------------------------------------------------------
echo "[2/4] Drafting skill implementation with Codex..."
DRAFT=$(invoke_codex \
"Generate a complete DevSquad skill named '${SKILL_NAME}' that does: ${DESCRIPTION}

Use EXACTLY these conventions observed in the codebase:
${RESEARCH}

Output three files using these EXACT delimiters (no extra text before or after each section):

=== FILE: SKILL.md ===
---
name: ${SKILL_NAME}
description: [one-sentence activation trigger — describe when Claude should use this skill]
version: 1.0.0
---

# [Skill Title]

[What the skill does]

## When to Use
[bullet list of triggers]

## Usage
\`\`\`bash
bash \"\${CLAUDE_PLUGIN_ROOT}/skills/${SKILL_NAME}/scripts/${SKILL_NAME}.sh\" [arguments]
\`\`\`

## Dependencies
- lib/state.sh — [purpose]
[add other libs as needed]

=== FILE: scripts/${SKILL_NAME}.sh ===
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=\"\$(cd \"\$(dirname \"\${BASH_SOURCE[0]:-\$0}\")\" && pwd)\"
PLUGIN_ROOT=\"\$(cd \"\${SCRIPT_DIR}/../../..\" && pwd)\"

source \"\${PLUGIN_ROOT}/lib/state.sh\"
[add other sources as needed]

[implementation using while-loop argument parsing, following codebase conventions]

=== FILE: commands/${SKILL_NAME}.md ===
---
description: [one-line command description]
argument-hint: \"[argument hint]\"
allowed-tools: [\"Read\", \"Write\", \"Bash\", \"Skill\"]
---

# DevSquad \$(echo "${SKILL_NAME}" | sed 's/-/ /g' | sed 's/\b./\u&/g')

Arguments: \$ARGUMENTS

Invoke the devsquad:${SKILL_NAME} skill and follow it exactly." \
  0 \
  120)

if [[ $? -ne 0 ]]; then
  echo "Error: Codex draft failed." >&2
  echo "$DRAFT" >&2
  exit 1
fi

echo "  Draft complete ($(echo "$DRAFT" | wc -l | tr -d ' ') lines)"

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
