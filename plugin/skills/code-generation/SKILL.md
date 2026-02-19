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
