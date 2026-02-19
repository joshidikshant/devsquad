---
phase: 03-enforcement-and-routing-skills
plan: 01
subsystem: code-generation-skill
tags: [skill-scaffold, bash, argument-parsing, code-generation]
dependency_graph:
  requires: []
  provides: [plugin/skills/code-generation/SKILL.md, plugin/skills/code-generation/scripts/generate-skill.sh, plugin/commands/generate.md]
  affects: [plugin/commands/]
tech_stack:
  added: []
  patterns: [BASH_SOURCE path resolution, while-loop argument parsing, stub-phase skeleton]
key_files:
  created:
    - plugin/skills/code-generation/SKILL.md
    - plugin/skills/code-generation/scripts/generate-skill.sh
    - plugin/commands/generate.md
  modified: []
decisions:
  - "BASH_SOURCE[0] path resolution instead of CLAUDE_PLUGIN_ROOT for portability when script runs via Bash tool"
  - "While-loop argument parsing (not getopts) for correct --name value capture with positional description accumulation"
  - "Lowercase + hyphen name derivation with 40-char truncation and sed cleanup for valid skill names"
metrics:
  duration: "~3 minutes"
  completed: "2026-02-19T07:52:00Z"
  tasks_completed: 2
  files_created: 3
  files_modified: 0
---

# Phase 03 Plan 01: Code Generation Skill Scaffold Summary

Established the code-generation skill scaffold: SKILL.md identity spec, /devsquad:generate command stub wired to the skill, and generate-skill.sh entry point with argument parsing, library sourcing, and clearly marked stub phases for Plans 02 and 03.

## What Was Built

### Files Created

**`plugin/skills/code-generation/SKILL.md`**
- Frontmatter: `name: code-generation`, `description`, `version: 1.0.0`
- Describes when to invoke the skill (user asks to "generate a skill", "create a new command", etc.)
- Documents usage, 5-step workflow, and library dependencies

**`plugin/commands/generate.md`**
- Command stub making `/devsquad:generate` available
- `description`, `argument-hint`, `allowed-tools` frontmatter
- Body invokes `devsquad:code-generation` skill with `$ARGUMENTS`

**`plugin/skills/code-generation/scripts/generate-skill.sh`**
- Resolves `PLUGIN_ROOT` from `BASH_SOURCE[0]` (not `CLAUDE_PLUGIN_ROOT`)
- Sources `state.sh`, `gemini-wrapper.sh`, `codex-wrapper.sh`
- While-loop argument parser: positional `<description>`, `--name`, `--dry-run`, `--help`
- Validates that description is non-empty, exits 1 with usage if missing
- Derives skill name (lowercase, hyphenated, 40-char max) from description
- Phase stubs: `[1/4] Research (Plan 02)`, `[2/4] Draft (Plan 02)`, `[3/4] Review (Plan 03)`

## Decisions Made

### 1. BASH_SOURCE path resolution (not CLAUDE_PLUGIN_ROOT)
When the script runs via Claude's Bash tool, `CLAUDE_PLUGIN_ROOT` may be unset. `BASH_SOURCE[0]` is always set relative to the script's actual location, making the resolution portable and reliable.

### 2. While-loop argument parsing
Consistent with Phase 2 Plan 01 decision for git-health.sh. The while-loop correctly consumes the next positional arg after `--name`, which a for-loop with shift cannot do inside a case statement.

### 3. Name derivation algorithm
`tr '[:upper:]' '[:lower:]'` → `tr ' ' '-'` → `tr -cd 'a-z0-9-'` → `sed 's/--*/-/g'` → strip leading/trailing hyphens → truncate at 40 chars. Produces clean, filesystem-safe, predictable skill names.

## Stub Sections Left for Plan 02 and Plan 03

| Phase | Label | Plan |
|-------|-------|------|
| Phase 1: Research | `# STUB — replaced by Plan 02` | 03-02 |
| Phase 2: Draft | `# STUB — replaced by Plan 02` | 03-02 |
| Phase 3: Review/Write | `# STUB — confirmation and file writing implemented in Plan 03` | 03-03 |

## Verification Results

All 8 checks from plan verification passed:

1. `plugin/skills/code-generation/SKILL.md` exists — PASS
2. `plugin/skills/code-generation/scripts/generate-skill.sh` exists — PASS
3. `plugin/commands/generate.md` exists — PASS
4. SKILL.md has `name`, `description`, `version` frontmatter — PASS
5. `bash -n generate-skill.sh` passes (no syntax errors) — PASS
6. Running with no args prints "Error: description is required" and exits 1 — PASS
7. Running with "bulk rename files" derives "bulk-rename-files" — PASS
8. `BASH_SOURCE` present; sources `state.sh`, `gemini-wrapper.sh`, `codex-wrapper.sh` — PASS

## Commits

| Task | Commit | Files |
|------|--------|-------|
| Task 1: SKILL.md + command stub | ec3db00 | plugin/skills/code-generation/SKILL.md, plugin/commands/generate.md |
| Task 2: generate-skill.sh | 6444d48 | plugin/skills/code-generation/scripts/generate-skill.sh |

## Deviations from Plan

None - plan executed exactly as written.

## Self-Check: PASSED
