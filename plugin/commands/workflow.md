---
description: Run a multi-step DevSquad workflow from a JSON definition file, with permission gates, auto-commits at checkpoints, and post-workflow validation
argument-hint: "--workflow <path-to-workflow.json> [--dry-run] [--skip-gates]"
allowed-tools: ["Read", "Write", "Bash", "Skill"]
---

# DevSquad Workflow

Arguments: $ARGUMENTS

Invoke the devsquad:workflow-orchestration skill with the provided arguments and follow it exactly.
