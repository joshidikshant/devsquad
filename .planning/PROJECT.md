# DevSquad Improvements Phase 2

## What This Is

DevSquad v1.0: A complete delegation enforcement and workflow automation layer for Claude Code. Ships four capabilities: a hook-based delegation advisor that intercepts bulk file reads and routes them to Gemini with token savings estimates; a git health skill detecting symlink/branch/change issues with JSON output; a code generation skill driving Gemini→Codex→review pipelines; and a workflow orchestration engine executing multi-step JSON workflows with gates, checkpoints, and dry-run support.

## Core Value

Preserve Claude context for decision-making by automating reading (Gemini), pattern detection (Codex), and routine workflows (orchestration).

## Requirements

### Validated

- ✓ Delegation Advisor Hook — Detect bulk file reading and suggest Gemini delegation — v1.0
- ✓ Git Health Check Skill — Find broken symlinks, orphaned branches, uncommitted changes — v1.0
- ✓ Code Generation Skill — Gemini research → Codex draft → review/write/verify pipeline — v1.0
- ✓ Workflow Orchestration — Multi-step JSON workflow engine with gates and checkpoints — v1.0

### Active

(Fresh for next milestone — run /gsd:new-milestone)

### Out of Scope

- OAuth/advanced auth (handled by Claude Code)
- Multi-user collaboration (single developer focus)
- Real-time sync (async workflows only)
- WFLO-04: Cleanup workflow for orphaned files/broken symlinks/stale branches (deferred to v2)

## Context

**v1.0 shipped 2026-02-19:**
- 87 files changed, +7,718 / -927 lines over 6 days
- All 4 phases complete: Delegation Advisor, Git Health, Code Generation, Workflow Orchestration
- 17/18 requirements satisfied (WFLO-04 explicitly deferred to v2)
- Known tech debt: Bash tool handler in pre-tool-use.sh is dead code (hooks.json matcher excludes Bash)

**DevSquad state:**
- Delegation enforcement hook fires on Read tool (threshold: 3 files/session)
- Git health skill has `--json` output mode with `total_issues` integer field
- Code generation skill: `/devsquad:generate <description>` end-to-end
- Workflow engine: `run-workflow.sh --workflow <json> [--dry-run]`

## Constraints

- **Integration**: Must work with existing DevSquad plugin architecture (hooks, skills, commands)
- **Non-blocking**: Hooks advise, not block (respect user autonomy)
- **Context-aware**: All improvements preserve Claude's finite context
- **Pattern-based**: Code generation only offered when patterns are detectable

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| 4-feature scope (not 7) | Git Health + Delegation Advisor are quick wins; Workflows are medium effort | ✓ Good — all 4 shipped cleanly |
| Advisor vs Enforcer | Suggest delegation without blocking to respect user agency | ✓ Good — advisory mode with token estimates |
| Skill-based generation | Use skills for code generation to match DevSquad conventions | ✓ Good — /devsquad:generate works end-to-end |
| WFLO-04 deferred | Cleanup workflow is complex; ship core orchestration engine first | ✓ Good — clean v2 scope |
| total_issues (not .status) | git-health.sh --json emits integer count, not status string | ✓ Fixed — lib-workflow.sh now reads correctly |

---
*Last updated: 2026-02-19 after v1.0 milestone*
