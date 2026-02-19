# DevSquad Phase 2 Roadmap

## Overview

This roadmap delivers four interconnected capabilities that together achieve DevSquad's core value: preserving Claude context by automating reading (Gemini), pattern detection (Codex), and routine workflows. Phases are ordered by dependency, with foundation capabilities enabling subsequent automation layers.

---

## Phase 1: Delegation Advisor

**Goal:** Claude detects when reading will consume context and autonomously suggests delegation to Gemini.

**Dependencies:** None (foundation phase)

**Requirements Mapped:**
- DELEG-01: Detect bulk reading operations
- DELEG-02: Suggest Gemini delegation with topic summary
- DELEG-03: Offer delegation in non-blocking manner
- DELEG-04: Track delegation acceptance metrics

**Success Criteria:**

1. When user reads 3+ files in sequence, Claude detects pattern and suggests Gemini for remaining reads
2. Delegation suggestion includes task summary and estimated context savings
3. Suggestion appears as optional notification (does not block user interaction)
4. System tracks which suggestions users accept/decline

---

## Phase 2: Git Health Check

**Goal:** Automated detection and repair of common repository problems that impede workflow.

**Dependencies:** Phase 1 (foundation; not blocking but improves efficiency)

**Requirements Mapped:**
- GHLT-01: Find broken symlinks in repository
- GHLT-02: Identify orphaned branches (merged but not deleted)
- GHLT-03: Detect uncommitted changes across workspace
- GHLT-04: Generate remediation suggestions for each issue
- GHLT-05: Execute cleanup (with user confirmation)

**Success Criteria:**

1. Health check discovers all broken symlinks and reports location/target
2. System identifies orphaned branches and distinguishes from active work branches
3. Uncommitted changes are found and categorized by severity (untracked, modified, staged)
4. Cleanup suggestions are actionable and reversible (user reviews before execution)
5. Post-cleanup verification confirms symlinks functional and branch state clean

---

## Phase 3: Code Generation

**Goal:** Transform natural language descriptions into executable DevSquad skills via automated research and drafting.

**Dependencies:** Phase 1 (uses Gemini delegation), Phase 2 (clean repo state improves generation)

**Requirements Mapped:**
- CGEN-01: Accept skill description in natural language
- CGEN-02: Research existing patterns in codebase (Gemini)
- CGEN-03: Draft skill implementation using Codex
- CGEN-04: Present draft for user review/iteration before writing
- CGEN-05: Skill generates itself (self-referential — code-generation skill is invocable via /devsquad:generate)

**Plans:** 3/3 plans complete

Plans:
- [ ] 03-01-PLAN-skill-scaffold.md — Create SKILL.md, command stub, and generate-skill.sh skeleton with argument parsing
- [ ] 03-02-PLAN-research-and-draft-pipeline.md — Implement Gemini research phase and Codex draft phase
- [ ] 03-03-PLAN-review-write-and-verify.md — Implement review loop, file writer, bash -n validation, and summary

**Success Criteria:**

1. User describes desired skill (e.g., "bulk rename files matching pattern") in 1-2 sentences
2. System scans codebase and identifies relevant patterns/libraries used
3. Codex generates working skill code matching DevSquad conventions
4. Draft is displayed for user review with [y/N/e] prompt before any files are written
5. New skill immediately available via `devsquad <skill-name>` after confirmation

---

## Phase 4: Workflow Orchestration

**Goal:** Chain multiple DevSquad capabilities into autonomous, feature-complete workflows that execute with minimal user intervention.

**Dependencies:** Phase 1 (delegation), Phase 2 (health checks), Phase 3 (generated skills)

**Requirements Mapped:**
- ORCH-01: Define feature workflow template (sequence of skills)
- ORCH-02: Implement user permission gates (confirm before destructive steps)
- ORCH-03: Auto-commit changes at workflow checkpoints
- ORCH-04: Cleanup and validation workflow on completion

**Success Criteria:**

1. User defines multi-step workflow (e.g., feature create → generate code → test → cleanup)
2. System prompts for confirmation before destructive operations (branch deletion, file removal)
3. Each workflow milestone auto-commits with generated message (reference issue/feature)
4. Post-workflow validation checks repo health, runs tests, reports results
5. Failed workflow steps are logged with rollback suggestions (user can revert to last checkpoint)

---

## Phase Coverage

| Phase | Category | Requirement Count | Status |
|-------|----------|-------------------|--------|
| 1 | Delegation Advisor | 4 | Complete |
| 2 | Git Health Check | 5 | Complete |
| 3 | 3/3 | Complete   | 2026-02-19 |
| 4 | Workflow Orchestration | 4 | Planned |

**Total Requirements:** 18/18 mapped
**Coverage:** 100% ✓

---

## Progress Tracking

```
Phase 1: [================] Complete
Phase 2: [================] Complete
Phase 3: [................] Not started
Phase 4: [................] Not started
```

---

## Key Dependencies

```
Phase 1 (Delegation Advisor)
    ↓
Phase 2 (Git Health Check)
    ↓ (enables clean state)
Phase 3 (Code Generation)
    ↓ (uses 1, 2, 3)
Phase 4 (Workflow Orchestration)
```

Phases 1-2 can execute in parallel. Phase 3 should follow 1-2. Phase 4 depends on all prior phases.

---

## Notes

- **Quick Mode:** 4 phases compress naturally around requirement categories; no artificial grouping
- **Parallelization:** Phases 1 and 2 are independent and can be developed in parallel
- **Auto-approve:** Roadmap generated in YOLO mode; ready for immediate planning
