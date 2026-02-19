---
phase: 04-workflow-orchestration
plan: "02"
subsystem: infra
tags: [bash, workflow, orchestration, jq, envsubst]

# Dependency graph
requires:
  - phase: 04-01
    provides: lib-workflow.sh helpers (workflow_gate, workflow_checkpoint, workflow_validate) and feature-workflow.json template
provides:
  - run-workflow.sh: main workflow driver script that reads JSON workflow definitions and executes steps sequentially
  - workflow.md: /devsquad:workflow command stub wired to devsquad:workflow-orchestration skill
affects:
  - 04-03-PLAN
  - any plan testing end-to-end workflow orchestration

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "envsubst-with-fallback: safe variable expansion using envsubst, perl, or bash-native substitution (priority order)"
    - "while-loop arg parsing consistent with Phase 2 locked decision"
    - "BASH_SOURCE[0]:-$0 for PLUGIN_ROOT resolution when run via Bash tool"
    - "atomic state.json writes via .tmp.$$ then mv — prevents partial write corruption"
    - "continue-on-failure: step failure appends to WORKFLOW_FAILED_STEPS but does not abort loop"

key-files:
  created:
    - plugin/skills/workflow-orchestration/scripts/run-workflow.sh
    - plugin/commands/workflow.md
  modified: []

key-decisions:
  - "envsubst with perl/bash fallback for safe variable expansion: envsubst not available on macOS by default; perl -pe with defined check leaves undefined vars intact; bash-native substitution as last resort — all three approaches avoid eval (no arbitrary code execution)"
  - "continue-on-failure step execution: step failure appends to WORKFLOW_FAILED_STEPS array and continues loop rather than aborting — enables partial workflow logging and produces FINAL_STATUS=partial on exit"
  - "workflow_validate called once after all steps (not inside loop): matches ORCH-04 requirement; single post-workflow validation gate rather than per-step validation"

patterns-established:
  - "run-workflow.sh pattern: DEVSQUAD_HOOK_DEPTH=1 export must be first non-comment line after set options"
  - "thin command stub pattern: workflow.md mirrors generate.md with description/argument-hint/allowed-tools frontmatter + single skill invocation body"

requirements-completed:
  - ORCH-01
  - ORCH-02
  - ORCH-03
  - ORCH-04

# Metrics
duration: 3min
completed: 2026-02-19
---

# Phase 4 Plan 02: Workflow Driver and Command Stub Summary

**JSON-driven workflow driver (run-workflow.sh) with permission gates, checkpoint commits, rollback hints, and /devsquad:workflow command stub**

## Performance

- **Duration:** ~3 min
- **Started:** 2026-02-19T10:00:38Z
- **Completed:** 2026-02-19T10:03:18Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments

- Created run-workflow.sh — reads JSON workflow definitions, executes steps sequentially, calls workflow_gate/workflow_checkpoint/workflow_validate from lib-workflow.sh at the correct moments
- Implemented safe variable expansion (envsubst with perl and bash-native fallbacks) to expand $WORKFLOW_NAME and $STEP_ID in args/commit_messages without using eval
- Created workflow.md command stub — thin wrapper matching generate.md pattern, wires /devsquad:workflow to devsquad:workflow-orchestration skill

## Task Commits

Each task was committed atomically:

1. **Task 1: Create run-workflow.sh main driver** - `62f3986` (feat)
2. **Task 2: Create workflow.md command stub** - `91451a2` (feat)

## Files Created/Modified

- `plugin/skills/workflow-orchestration/scripts/run-workflow.sh` - Main workflow driver script: reads JSON, iterates steps, calls gate/checkpoint/validate helpers, prints rollback hints on failure
- `plugin/commands/workflow.md` - Command stub for /devsquad:workflow, thin-wrapper referencing devsquad:workflow-orchestration skill

## Decisions Made

- **envsubst with perl/bash fallback:** envsubst is not installed on macOS by default. Implemented priority fallback chain: envsubst (if available) → perl -pe with defined check (leaves undefined vars intact) → bash-native string substitution for WORKFLOW_NAME and STEP_ID. All three avoid eval, preserving the security intent from the plan.
- **continue-on-failure loop:** Step failures append to WORKFLOW_FAILED_STEPS and continue rather than aborting. This enables partial workflow logging and produces an informative summary. Exit code reflects FINAL_STATUS (complete vs partial).
- **workflow_validate after loop:** Called once after all steps complete, not inside the step loop, matching ORCH-04 requirement for post-workflow validation.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] envsubst not available on macOS**
- **Found during:** Task 1 verification (dry-run smoke test)
- **Issue:** Plan specified `envsubst` for safe variable expansion, but `envsubst` is not installed on macOS (part of GNU gettext, not bundled). Dry-run test returned exit 127.
- **Fix:** Implemented fallback chain — envsubst (if available) → perl -pe with `defined $ENV{$1} ? $ENV{$1} : $&` (leaves undefined vars as-is) → bash-native `${var//pattern/replacement}` for the two known variables (WORKFLOW_NAME, STEP_ID)
- **Files modified:** plugin/skills/workflow-orchestration/scripts/run-workflow.sh
- **Verification:** Dry-run test passed with 4/4 steps printed; perl regex confirmed to preserve undefined variables
- **Committed in:** 62f3986 (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (Rule 3 - blocking)
**Impact on plan:** Security intent (no eval) fully preserved across all fallback paths. No scope creep.

## Issues Encountered

- envsubst missing on macOS — resolved via perl/bash fallback chain (documented above as deviation)

## Next Phase Readiness

- run-workflow.sh is executable and passes bash -n
- All four lib-workflow.sh helper calls verified present (gate, checkpoint, validate, state init)
- /devsquad:workflow command stub ready for use via Claude slash commands
- Phase 4 Plan 03 can proceed (integration tests and end-to-end workflow verification)

---
*Phase: 04-workflow-orchestration*
*Completed: 2026-02-19*

## Self-Check: PASSED

- FOUND: plugin/skills/workflow-orchestration/scripts/run-workflow.sh
- FOUND: plugin/commands/workflow.md
- FOUND: .planning/phases/04-workflow-orchestration/04-02-SUMMARY.md
- FOUND: commit 62f3986 (Task 1: run-workflow.sh)
- FOUND: commit 91451a2 (Task 2: workflow.md)
