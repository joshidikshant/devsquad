---
name: git-health
description: Scan the repository for health issues (broken symlinks, orphaned branches, uncommitted changes) and suggest cleanup actions
argument-hint: Optional: [--json] [--check symlinks|branches|changes] [--fix]
allowed-tools: ["Read", "Bash", "Skill"]
---

# Git Health Check

Run the DevSquad git health scan.

Arguments: $ARGUMENTS

Invoke the git-health skill and present the findings to the user.
