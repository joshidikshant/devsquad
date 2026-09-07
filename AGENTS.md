# Working on DevSquad

Read [CONTRIBUTING.md](CONTRIBUTING.md) before changing code. Run
`bash test/run.sh` before every commit; core changes also need the relevant
offline Python tests. Preserve the existing Bash 3.2 and optional-jq contracts.

## Continuing the engineering-team build

This build is already underway. After a usage limit, interrupted session or
handoff, read these files before starting work:

1. [RESUME.md](docs/plans/engineering-team/RESUME.md): latest checkpoint,
   verified results, open findings and exact next action.
2. [backlog.json](docs/plans/engineering-team/backlog.json): milestone status
   and evidence.
3. [SOL-HANDOFF.md](docs/plans/engineering-team/SOL-HANDOFF.md): the full
   authorized delivery scope and acceptance criteria.

Compare the recovery note with `git status` and recent commits; retain work
newer than the note. Continue on `codex/engineering-team` unless the user
directs otherwise. Do not restart the architecture exercise or discard
implementation to return to `main`.

Checkpoint coherent partial work and update RESUME.md before long probes or
handoffs. Record failures and incomplete gates truthfully. Finish a pause with
a clean tree; never use a stash as the recovery mechanism. Keep raw provider
diagnostics and credentials outside tracked evidence.

Usage limits do not authorize buying credits, redeeming reset credits, using
a paid API fallback or changing global AI settings. The saved artifacts must
allow the next session to resume without relying on conversation memory.
