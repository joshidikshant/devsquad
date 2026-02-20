---
name: generate
description: Generate a new DevSquad skill from a natural language description using Gemini research and Codex drafting
argument-hint: "<description of the skill you want to create> [--name skill-name] [--dry-run]"
allowed-tools: ["Read", "Write", "Bash", "Skill"]
---

# DevSquad Generate

Arguments: $ARGUMENTS

Invoke the devsquad:code-generation skill with the provided description and follow it exactly.
