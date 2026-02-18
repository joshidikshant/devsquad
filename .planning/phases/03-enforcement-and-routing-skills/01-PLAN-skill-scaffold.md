---
phase: 03-enforcement-and-routing-skills
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - plugin/skills/code-generation/SKILL.md
  - plugin/skills/code-generation/scripts/generate-skill.sh
  - plugin/commands/generate.md
autonomous: true
requirements:
  - CGEN-01

must_haves:
  truths:
    - "plugin/skills/code-generation/ directory exists with SKILL.md and scripts/generate-skill.sh"
    - "generate-skill.sh accepts a positional description argument and optional --name flag"
    - "generate-skill.sh resolves PLUGIN_ROOT from BASH_SOURCE[0], not from CLAUDE_PLUGIN_ROOT"
    - "plugin/commands/generate.md exists and invokes the devsquad:code-generation skill"
    - "Running generate-skill.sh with no arguments prints usage and exits 1"
    - "Running generate-skill.sh with a description argument proceeds past validation (no crash)"
  artifacts:
    - path: "plugin/skills/code-generation/SKILL.md"
      provides: "Skill spec with frontmatter (name, description, version) and prose"
      contains: "name: code-generation"
    - path: "plugin/skills/code-generation/scripts/generate-skill.sh"
      provides: "Main entry point script"
      contains: "BASH_SOURCE"
    - path: "plugin/commands/generate.md"
      provides: "Thin command stub for /devsquad:generate"
      contains: "devsquad:code-generation"
  key_links:
    - from: "plugin/commands/generate.md"
      to: "plugin/skills/code-generation/SKILL.md"
      via: "Invoke the devsquad:code-generation skill"
      pattern: "devsquad:code-generation"
    - from: "plugin/skills/code-generation/scripts/generate-skill.sh"
      to: "plugin/lib/gemini-wrapper.sh"
      via: "source statement"
      pattern: "source.*gemini-wrapper\\.sh"
---

<objective>
Create the code-generation skill scaffold: SKILL.md spec, the command stub that makes /devsquad:generate available, and the generate-skill.sh entry point with argument parsing and library sourcing. The script body will stub out the three phases (research, draft, write) so Plan 02 can fill them in without touching structure.

Purpose: Establishes the skill's identity, invocation path, and script skeleton. All subsequent plans add implementation inside this skeleton.
Output: plugin/skills/code-generation/ directory, plugin/commands/generate.md, working argument parser.
</objective>

<execution_context>
@/Users/Dikshant/.claude/get-shit-done/workflows/execute-plan.md
@/Users/Dikshant/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@plugin/skills/git-health/SKILL.md
@plugin/commands/git-health.md
@plugin/skills/git-health/scripts/git-health.sh
@plugin/lib/gemini-wrapper.sh
@plugin/lib/codex-wrapper.sh
@plugin/lib/state.sh
</context>

<tasks>

<task type="auto">
  <name>Task 1: Create SKILL.md and command stub</name>
  <files>plugin/skills/code-generation/SKILL.md</files>
  <files>plugin/commands/generate.md</files>
  <action>
Create directory plugin/skills/code-generation/scripts/ (mkdir -p).

Write plugin/skills/code-generation/SKILL.md with this exact content:

```
---
name: code-generation
description: This skill should be used when the user asks to "generate a skill", "create a new command", "build a devsquad skill", or describes a new automation they want as a slash command.
version: 1.0.0
---

# Code Generation Skill

Transforms a natural language description into a working DevSquad skill by researching existing patterns with Gemini and drafting implementation with Codex.

## When to Use

Use this skill when the user:
- Describes an automation they want as a slash command (e.g., "bulk rename files matching pattern")
- Asks to "generate a skill" or "create a new DevSquad command"
- Wants to add a new capability to DevSquad without writing it manually

## Usage

```bash
bash "${CLAUDE_PLUGIN_ROOT}/skills/code-generation/scripts/generate-skill.sh" "<description>"
# or with explicit name
bash "${CLAUDE_PLUGIN_ROOT}/skills/code-generation/scripts/generate-skill.sh" "<description>" --name my-skill-name
```

## What It Does

1. Derives a skill name from the description (or uses --name)
2. Uses Gemini to research existing DevSquad patterns (skill structure, argument conventions, library usage)
3. Uses Codex to draft SKILL.md, the implementation script, and the command stub
4. Displays the full draft for user review
5. On confirmation, writes files atomically and runs bash -n validation

## Dependencies

- lib/gemini-wrapper.sh — Codebase pattern research via invoke_gemini_with_files
- lib/codex-wrapper.sh — Implementation drafting via invoke_codex
- lib/state.sh — Atomic file writes via write_state
```

Write plugin/commands/generate.md with this exact content:

```
---
description: Generate a new DevSquad skill from a natural language description using Gemini research and Codex drafting
argument-hint: "<description of the skill you want to create> [--name skill-name] [--dry-run]"
allowed-tools: ["Read", "Write", "Bash", "Skill"]
---

# DevSquad Generate

Arguments: $ARGUMENTS

Invoke the devsquad:code-generation skill with the provided description and follow it exactly.
```
  </action>
  <verify>bash -c "test -f plugin/skills/code-generation/SKILL.md && grep -q 'name: code-generation' plugin/skills/code-generation/SKILL.md && echo OK"
bash -c "test -f plugin/commands/generate.md && grep -q 'devsquad:code-generation' plugin/commands/generate.md && echo OK"</verify>
  <done>Both files exist. SKILL.md contains "name: code-generation" frontmatter. generate.md contains "devsquad:code-generation" invocation text.</done>
</task>

<task type="auto">
  <name>Task 2: Create generate-skill.sh with argument parsing and library sourcing</name>
  <files>plugin/skills/code-generation/scripts/generate-skill.sh</files>
  <action>
Write plugin/skills/code-generation/scripts/generate-skill.sh with this exact content. Note PLUGIN_ROOT is resolved from BASH_SOURCE[0] (not CLAUDE_PLUGIN_ROOT) per Pitfall 1 in research. Argument parsing uses while-loop per Phase 2 locked decision. Library sourcing follows verified pattern from git-health.sh.

```bash
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
```

After writing, make it executable:
```bash
chmod +x plugin/skills/code-generation/scripts/generate-skill.sh
```
  </action>
  <verify>bash -n plugin/skills/code-generation/scripts/generate-skill.sh && echo "syntax OK"
bash plugin/skills/code-generation/scripts/generate-skill.sh 2>&1 | grep -q "description is required" && echo "validation OK"
bash plugin/skills/code-generation/scripts/generate-skill.sh "bulk rename files" 2>&1 | grep -q "bulk-rename-files" && echo "name derivation OK"</verify>
  <done>bash -n passes (no syntax errors). Running with no args prints "Error: description is required" and exits 1. Running with "bulk rename files" derives skill name "bulk-rename-files" and prints it.</done>
</task>

</tasks>

<verification>
1. Directory structure correct: plugin/skills/code-generation/SKILL.md and plugin/skills/code-generation/scripts/generate-skill.sh exist
2. Command stub exists at plugin/commands/generate.md
3. SKILL.md frontmatter has name, description, version fields
4. generate-skill.sh passes bash -n (syntax valid)
5. Script validates empty description and exits 1
6. Script derives hyphenated skill name from positional argument
7. Script resolves PLUGIN_ROOT from BASH_SOURCE[0] (grep "BASH_SOURCE" in file)
8. Script sources all three libraries (state.sh, gemini-wrapper.sh, codex-wrapper.sh)
</verification>

<success_criteria>
- /devsquad:generate command stub is wired to devsquad:code-generation skill
- generate-skill.sh accepts description argument and derives skill-name
- Script skeleton has clearly marked stubs for Plan 02 (research+draft) and Plan 03 (write)
- bash -n validation passes on all new files
- CGEN-01 satisfied: skill accepts natural language description
</success_criteria>

<output>
After completion, create .planning/phases/03-enforcement-and-routing-skills/03-01-SUMMARY.md with:
- Files created (3 files, paths)
- Key decisions made (name derivation algorithm, BASH_SOURCE path resolution)
- Stub sections left for Plan 02 and Plan 03
- Verification results
</output>
