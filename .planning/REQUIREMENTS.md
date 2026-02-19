# Requirements: DevSquad Phase 2

**Defined:** 2026-02-13
**Core Value:** Preserve Claude context for decision-making by automating reading, pattern detection, and routine workflows.

## v1 Requirements

Requirements for Phase 2 release. Each maps to roadmap phases.

### Delegation Advisor

- [ ] **DELEG-01**: Hook detects when user reads >3 files in a session
- [ ] **DELEG-02**: Hook suggests Gemini delegation with example syntax
- [ ] **DELEG-03**: Hook offers to use Gemini instead (non-blocking)
- [ ] **DELEG-04**: Hook does not block execution (advisory only)

### Git Health

- [ ] **GHLT-01**: Skill finds all broken symlinks in repository
- [ ] **GHLT-02**: Skill detects orphaned branches (no commits in N days)
- [ ] **GHLT-03**: Skill identifies uncommitted changes diverging from remote
- [ ] **GHLT-04**: Skill reports findings in structured format
- [ ] **GHLT-05**: Skill suggests cleanup actions for each issue

### Code Generation

- [x] **CGEN-01**: Skill receives description of desired command
- [x] **CGEN-02**: Skill uses Gemini to research DevSquad command patterns
- [x] **CGEN-03**: Skill uses Codex to draft command implementation
- [x] **CGEN-04**: Skill presents draft for user review/iteration
- [x] **CGEN-05**: Skill generates similar skill for skill development

### Workflow Orchestration

- [x] **WFLO-01**: Feature workflow orchestrates research → generate → review → commit
- [x] **WFLO-02**: Workflow handles user input/approval gates
- [x] **WFLO-03**: Workflow auto-commits with semantic messages
- [ ] **WFLO-04**: Cleanup workflow finds and fixes repo issues automatically

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Advanced Code Generation

- **CGEN-06**: Detect when new code patterns match existing code → suggest Codex
- **CGEN-07**: Generate MCP integrations from specification
- **CGEN-08**: Generate test files matching existing patterns

### Workflow Extensions

- **WFLO-05**: Branch workflow for experimental features
- **WFLO-06**: Performance optimization workflow
- **WFLO-07**: Documentation generation workflow

## Out of Scope

| Feature | Reason |
|---------|--------|
| Automatic code review | Handled by human review step in workflow |
| IDE integration | Claude Code plugin focus only |
| Real-time collaboration | Single developer workflow |
| Machine learning for pattern detection | Heuristics sufficient for current needs |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| DELEG-01 | Phase 1 | Pending |
| DELEG-02 | Phase 1 | Pending |
| DELEG-03 | Phase 1 | Pending |
| DELEG-04 | Phase 1 | Pending |
| GHLT-01 | Phase 2 | Pending |
| GHLT-02 | Phase 2 | Pending |
| GHLT-03 | Phase 2 | Pending |
| GHLT-04 | Phase 2 | Pending |
| GHLT-05 | Phase 2 | Pending |
| CGEN-01 | Phase 3 | Complete |
| CGEN-02 | Phase 3 | Complete |
| CGEN-03 | Phase 3 | Complete |
| CGEN-04 | Phase 3 | Complete |
| CGEN-05 | Phase 3 | Complete |
| WFLO-01 | Phase 4 | Complete |
| WFLO-02 | Phase 4 | Complete |
| WFLO-03 | Phase 4 | Complete |
| WFLO-04 | Phase 4 | Pending |

**Coverage:**
- v1 requirements: 18 total
- Mapped to phases: 18
- Unmapped: 0 ✓

---
*Requirements defined: 2026-02-13*
*Last updated: 2026-02-13 after initial definition*
