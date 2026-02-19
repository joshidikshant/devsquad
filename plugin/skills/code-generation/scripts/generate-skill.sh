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
# Phase 3: Review — interactive [y/N/e] loop
# ---------------------------------------------------------------------------

# parse_draft_files: parse DRAFT by "=== FILE: <name> ===" delimiters.
# Writes each file section to the output directory atomically.
# Arguments: $1=draft content, $2=output root dir
write_draft_files() {
  local draft="$1"
  local out_dir="$2"
  local current_file=""
  local buffer=""
  local files_written=()

  # Helper: strip leading and trailing blank lines from a string
  strip_blank_lines() {
    local text="$1"
    # Remove leading blank lines
    text="$(printf '%s' "$text" | awk 'NF{found=1} found{print}')"
    # Remove trailing blank lines via awk reading in reverse is complex;
    # use a simple Python/perl if available, else basic awk
    if command -v perl &>/dev/null; then
      text="$(printf '%s' "$text" | perl -0777 -pe 's/\s+$/\n/')"
    else
      text="$(printf '%s\n' "$text" | awk 'NF{last=NR} NR<=last{print}' | head -"$(printf '%s' "$text" | awk 'NF{c=NR} END{print c}')")"
    fi
    printf '%s' "$text"
  }

  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" =~ ^===\ FILE:\ (.+)\ ===$ ]]; then
      # Flush previous section
      if [[ -n "$current_file" && -n "$buffer" ]]; then
        local dest="${out_dir}/${current_file}"
        mkdir -p "$(dirname "$dest")"
        local stripped
        stripped="$(strip_blank_lines "$buffer")"
        printf '%s\n' "$stripped" > "$dest"
        files_written+=("$dest")
        buffer=""
      fi
      current_file="${BASH_REMATCH[1]}"
    else
      buffer="${buffer}${line}
"
    fi
  done <<< "$draft"

  # Flush final section
  if [[ -n "$current_file" && -n "$buffer" ]]; then
    local dest="${out_dir}/${current_file}"
    mkdir -p "$(dirname "$dest")"
    local stripped
    stripped="$(strip_blank_lines "$buffer")"
    printf '%s\n' "$stripped" > "$dest"
    files_written+=("$dest")
  fi

  # Make any .sh files executable
  for f in "${files_written[@]:-}"; do
    if [[ "$f" == *.sh ]]; then
      chmod +x "$f"
    fi
  done

  # Return list of written files via global
  WRITTEN_FILES=("${files_written[@]:-}")
}

# validate_shell_scripts: run bash -n on each .sh file in WRITTEN_FILES
validate_shell_scripts() {
  local errors=0
  for f in "${WRITTEN_FILES[@]:-}"; do
    if [[ "$f" == *.sh ]]; then
      if bash -n "$f" 2>/dev/null; then
        echo "  [ok] syntax valid: $(basename "$f")"
      else
        echo "  [WARN] syntax error in: $f" >&2
        bash -n "$f" >&2 || true
        errors=$((errors + 1))
      fi
    fi
  done
  return $errors
}

# Target directory: skills/<name>/ under the plugin root
SKILL_DIR="${PLUGIN_ROOT}/skills/${SKILL_NAME}"

echo "[3/4] Draft ready for review..."
echo ""
echo "=== GENERATED DRAFT ==="
echo "$DRAFT"
echo "========================"
echo ""

if [[ "$DRY_RUN" == "true" ]]; then
  echo "[dry-run] Skipping write phase. Files would be written to: ${SKILL_DIR}/"
  echo "Done."
  exit 0
fi

# Interactive review loop
while true; do
  printf "Accept draft and write files? [y/N/e] (y=yes, n=abort, e=edit/feedback): "
  read -r REPLY </dev/tty

  case "${REPLY,,}" in
    y|yes)
      echo ""
      echo "[4/4] Writing skill files..."

      write_draft_files "$DRAFT" "${PLUGIN_ROOT}"

      if [[ ${#WRITTEN_FILES[@]} -eq 0 ]]; then
        echo "Error: No files were parsed from draft. Check delimiter format." >&2
        exit 1
      fi

      echo "  Written files:"
      for f in "${WRITTEN_FILES[@]}"; do
        echo "    ${f#${PLUGIN_ROOT}/}"
      done

      echo ""
      echo "  Validating generated scripts..."
      if ! validate_shell_scripts; then
        echo "Warning: One or more generated scripts have syntax errors." >&2
        echo "Files have been written — fix them manually or re-run generate with feedback." >&2
        exit 2
      fi

      echo ""
      echo "=== Skill '${SKILL_NAME}' generated successfully ==="
      echo "  Skill dir : ${SKILL_DIR}/"
      echo "  Command   : /devsquad:${SKILL_NAME}"
      echo ""
      break
      ;;

    n|no|"")
      echo ""
      echo "Aborted. No files written."
      exit 0
      ;;

    e|edit)
      echo ""
      printf "Enter feedback for Codex (describe what to change): "
      read -r FEEDBACK </dev/tty
      if [[ -z "$FEEDBACK" ]]; then
        echo "No feedback provided — keeping current draft."
        continue
      fi

      echo ""
      echo "[2/4] Re-drafting with feedback..."
      DRAFT=$(invoke_codex \
"Generate a complete DevSquad skill named '${SKILL_NAME}' that does: ${DESCRIPTION}

Use EXACTLY these conventions observed in the codebase:
${RESEARCH}

Previous draft was rejected. User feedback:
${FEEDBACK}

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

=== FILE: skills/${SKILL_NAME}/scripts/${SKILL_NAME}.sh ===
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

# DevSquad $(echo "${SKILL_NAME}" | sed 's/-/ /g' | sed 's/\b./\u&/g')

Arguments: \$ARGUMENTS

Invoke the devsquad:${SKILL_NAME} skill and follow it exactly." \
        0 \
        120)

      if [[ $? -ne 0 ]]; then
        echo "Error: Codex re-draft failed." >&2
        echo "$DRAFT" >&2
        exit 1
      fi

      echo "  Re-draft complete ($(echo "$DRAFT" | wc -l | tr -d ' ') lines)"
      echo ""
      echo "=== REVISED DRAFT ==="
      echo "$DRAFT"
      echo "====================="
      echo ""
      ;;

    *)
      echo "Please enter y, n, or e."
      ;;
  esac
done
