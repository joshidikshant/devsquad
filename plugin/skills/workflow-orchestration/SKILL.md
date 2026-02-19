---
name: workflow-orchestration
description: This skill should be used when the user asks to "run a workflow", "chain skills together", "execute a feature workflow", or wants to automate a multi-step process (create feature branch -> generate code -> test -> clean up).
version: 1.0.0
---

# Workflow Orchestration Skill

Chains multiple DevSquad skills into an autonomous, feature-complete workflow that executes with minimal user intervention. Reads a JSON workflow definition file, executes steps sequentially, gates destructive operations with user permission prompts, auto-commits at checkpoints, and validates the result.

## When to Use

Use this skill when the user:
- Wants to run a pre-defined multi-step workflow (e.g., "run the feature workflow for issue-42")
- Asks to "chain" multiple skills or automate a sequence of operations
- Mentions a workflow file (e.g., "use feature-workflow.json")
- Wants a repeatable, auditable process for common development patterns

## Invocation

```bash
bash plugin/skills/workflow-orchestration/scripts/run-workflow.sh \
  --workflow plugin/skills/workflow-orchestration/templates/feature-workflow.json \
  [--dry-run] \
  [--skip-gates]
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `--workflow <path>` | Yes | Path to the JSON workflow definition file |
| `--dry-run` | No | Print steps without executing them |
| `--skip-gates` | No | Skip destructive-step confirmation prompts (use with caution) |

## Workflow JSON Format

See `templates/feature-workflow.json` for a complete example. Each step has:
- `id` - unique step identifier (used as checkpoint key)
- `skill` - shell command or script to invoke
- `args` - string of arguments passed to the skill
- `destructive` - boolean, gates with user prompt if true
- `checkpoint` - boolean, auto-commits and records hash if true
- `commit_message` - optional message template (supports `$WORKFLOW_NAME`, `$STEP_ID` env vars)
