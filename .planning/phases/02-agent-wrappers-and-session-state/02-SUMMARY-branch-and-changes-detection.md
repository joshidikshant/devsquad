---
phase: "02-agent-wrappers-and-session-state"
plan: "02"
subsystem: "git-health"
tags: ["git", "bash", "branch-detection", "change-detection", "structured-output"]
dependency_graph:
  requires: ["01-PLAN-git-health-skill-scaffold.md"]
  provides: ["check-branches.sh", "check-changes.sh", "full git-health.sh integration"]
  affects: ["plugin/skills/git-health/scripts/"]
tech_stack:
  added: []
  patterns: ["pipe-delimited output protocol", "git plumbing commands", "JSON generation via heredoc"]
key_files:
  created:
    - plugin/skills/git-health/scripts/check-branches.sh
    - plugin/skills/git-health/scripts/check-changes.sh
  modified:
    - plugin/skills/git-health/scripts/git-health.sh
decisions:
  - "git merge-base --is-ancestor is the correct POSIX check for merged branch detection (exits 0 = merged)"
  - "git for-each-ref --format='%(upstream:short)' to get tracking remote without awk/grep"
  - "Pipe delimiter for output consistent across all sub-check scripts for structured parsing"
  - "AHEAD check first (highest severity — unpushed commits mean work at risk)"
  - "basename comparison for secret file warning avoids false matches on path components"
metrics:
  duration: "3 minutes"
  completed_date: "2026-02-18"
  tasks_completed: 3
  tasks_total: 3
  files_created: 2
  files_modified: 1
---

# Phase 02 Plan 02: Branch and Uncommitted Change Detection Summary

## One-Liner

Orphaned branch detection via `git merge-base --is-ancestor` and uncommitted change categorization (STAGED/MODIFIED/UNTRACKED/AHEAD) with secret file warnings wired into git-health.sh with structured JSON output and cleanup suggestions.

## What Was Built

### check-branches.sh
Detects two categories of orphaned branches:
- **Merged branches**: uses `git merge-base --is-ancestor` — exits 0 if branch is fully merged into main/master
- **Stale branches**: compares last commit Unix timestamp against configurable `--days` threshold (default 30)

Outputs `count=N` followed by pipe-delimited lines: `BRANCH_NAME|DAYS_OLD|REASON`.
Excludes current branch, default branch (main/master), and HEAD from candidates.
Handles non-git directories gracefully with `count=0`.

### check-changes.sh
Categorizes uncommitted work into four severity levels:
- `AHEAD` — local commits not pushed (highest severity, unpushed work at risk)
- `STAGED` — files in git index not yet committed
- `MODIFIED` — tracked files with working-tree changes
- `UNTRACKED` — untracked files (respects .gitignore via `ls-files --others --exclude-standard`)

Adds `WARNING: may contain secrets` flag to files matching `.env`, `.env.*`, `*.pem`, `*.key`, `*_rsa`, `*_dsa`, `credentials*`, `secrets*` patterns.
Outputs `count=N` followed by pipe-delimited lines: `CATEGORY|FILE_PATH[|WARNING]`.

### git-health.sh (extended)
- Replaced `run_branch_check()` stub with real call to `check-branches.sh` passing `--days`
- Replaced `run_changes_check()` stub with real call to `check-changes.sh`
- Added `--days N` argument to git-health.sh CLI, forwarded to branch checker
- Extended JSON output (`--json`) with full branch and changes issue arrays (GHLT-04)
- Extended human output with actionable cleanup suggestions per issue (GHLT-05):
  - Merged branch: `SAFE DELETE: git branch -d "branch-name"`
  - Stale branch: `REVIEW THEN DELETE: git branch -D "branch-name"`
  - Modified file: `COMMIT or STASH: git add <file> && git commit`
  - Secret-pattern file: warning displayed inline

## Requirements Covered

| Requirement | Implementation |
|-------------|----------------|
| GHLT-02: Orphaned branch detection | check-branches.sh merged + stale logic |
| GHLT-03: Uncommitted change categorization | check-changes.sh STAGED/MODIFIED/UNTRACKED/AHEAD |
| GHLT-04: Structured JSON output | Extended JSON heredoc with all three check arrays |
| GHLT-05: Cleanup suggestions | Human output includes actionable git commands per issue |

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| branches-1 | ba8fa77 | feat(02-02): create orphaned branch checker script |
| branches-2 | e9aacc8 | feat(02-02): create uncommitted changes checker script |
| branches-3 | 5654f58 | feat(02-02): wire branch and changes checks into git-health.sh |

## Deviations from Plan

None — plan executed exactly as written.

## Verification Results

All success criteria met:

- [x] check-branches.sh detects merged branches (count=1 in functional test)
- [x] check-branches.sh detects stale branches (via --days threshold)
- [x] check-branches.sh excludes current branch and default branch
- [x] check-changes.sh detects STAGED, MODIFIED, UNTRACKED files
- [x] check-changes.sh adds WARNING flag to .env files
- [x] git-health.sh human output includes cleanup suggestions for all three checks
- [x] git-health.sh --json produces valid JSON with all three checks populated
- [x] git-health.sh --days N passes N to branch checker
- [x] All scripts pass bash -n syntax check
- [x] Non-git directories handled gracefully (exit 0, count=0)
- [x] Each task committed individually

## Self-Check: PASSED

- check-branches.sh: FOUND at plugin/skills/git-health/scripts/check-branches.sh
- check-changes.sh: FOUND at plugin/skills/git-health/scripts/check-changes.sh
- git-health.sh: FOUND at plugin/skills/git-health/scripts/git-health.sh
- 02-SUMMARY: FOUND at .planning/phases/02-agent-wrappers-and-session-state/02-SUMMARY-branch-and-changes-detection.md
- Commit ba8fa77: FOUND (feat: create orphaned branch checker script)
- Commit e9aacc8: FOUND (feat: create uncommitted changes checker script)
- Commit 5654f58: FOUND (feat: wire branch and changes checks into git-health.sh)
