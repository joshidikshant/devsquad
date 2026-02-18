---
name: git-health
description: Finds and reports repository health issues including broken symlinks, orphaned branches, and uncommitted changes. Suggests cleanup actions for each issue.
version: 1.0.0
---

# Git Health Skill

Scans the repository for common issues that impede workflow and reports actionable remediation steps.

## Checks Performed

1. **Broken Symlinks** (GHLT-01): Finds symlinks whose targets do not exist
2. **Orphaned Branches** (GHLT-02): Detects branches with no new commits in N days (default: 30)
3. **Uncommitted Changes** (GHLT-03): Identifies changes not yet pushed to remote, categorized by severity

## When to Use

Invoke before starting major work, after a long absence from a project, or when experiencing unexplained tool failures (often caused by broken symlinks).

## Usage

```bash
bash "${CLAUDE_PLUGIN_ROOT}/skills/git-health/scripts/git-health.sh"
```

For machine-readable output:
```bash
bash "${CLAUDE_PLUGIN_ROOT}/skills/git-health/scripts/git-health.sh" --json
```

To run only a specific check:
```bash
bash "${CLAUDE_PLUGIN_ROOT}/skills/git-health/scripts/git-health.sh" --check symlinks
bash "${CLAUDE_PLUGIN_ROOT}/skills/git-health/scripts/git-health.sh" --check branches
bash "${CLAUDE_PLUGIN_ROOT}/skills/git-health/scripts/git-health.sh" --check changes
```

## Output Format

### Human-Readable (default)
```
=== Git Health Check ===

Broken Symlinks: 2 issue(s) found
  .git/hooks/pre-commit -> ../missing-hook  [DELETE: git rm .git/hooks/pre-commit]
  node_modules/.bin/tsc -> ../typescript/bin/tsc  [REINSTALL: npm install]

Orphaned Branches: 1 issue(s) found
  feature/old-experiment (last commit: 45 days ago)  [DELETE: git branch -d feature/old-experiment]

Uncommitted Changes: 3 issue(s) found
  [STAGED]    src/auth.ts
  [MODIFIED]  src/config.ts
  [UNTRACKED] .env.local  [WARNING: may contain secrets]

Summary: 6 issue(s) across 3 check(s). Run with --fix to apply suggestions.
```

### JSON Format (with --json)
```json
{
  "timestamp": "2026-02-18T10:00:00Z",
  "checks": {
    "symlinks": { "count": 2, "issues": [...] },
    "branches": { "count": 1, "issues": [...] },
    "changes": { "count": 3, "issues": [...] }
  },
  "total_issues": 6
}
```

## Dependencies

- lib/state.sh — State management and project dir resolution
- git CLI — All git operations
- find — Symlink traversal
