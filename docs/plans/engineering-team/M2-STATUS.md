# M2 implementation status

M2 is **in progress**. The store checkpoint is `0c2929c`; the bounded
supervisor lifecycle checkpoints are `0ee4cc0` and `df955f4`. Public detached service
operations and end-to-end shell persistence have not started.

| Store requirement | Evidence | Status |
|---|---|---|
| SQLite WAL and packaged schema migration | Fresh install opens the packaged migration; four independent processes concurrently initialize one database | verified offline |
| Canonical request idempotency before mutable snapshot resolution | Concurrent connections return one run for the same key/body; a changed body conflicts | verified offline |
| Canonical project identity | Main and linked Git worktrees resolve to the same absolute common directory and project ID | verified offline |
| Preparing-owner fencing and cancellation | Runs remain `queued` with a private `preparing` phase; stale completion after cancel conflicts; generic events cannot bypass the fence | verified offline |
| Transactional projections and events | Compare-and-swap run version and append-only event commit together under concurrent writers | verified offline |
| Atomic hash-verified artifacts | Content-addressed files finalize before reference; references increment run version with an event; duplicates and terminal mutation cannot clobber prior content | verified offline |
| Schema migration and future refusal | A real version-1 fixture upgrades to version 2; newer unsupported versions are rejected | verified offline |
| Supervisor and writer fencing | Transactional claims allow one supervisor and one active writer per worktree; ambiguous ownership retains the database fence | verified offline |
| Strong process identity and recovery | Darwin start second+microsecond identity is stable; live children remain owned without relaunch; dead and reused identities receive distinct recovery dispositions and reused IDs are never signalled | verified offline |
| Bounded process lifecycle | Direct argv runs in a new session; heartbeat, PID/PGID/start identity, token and package digest persist; stdout/stderr drain continuously with truncation and full-stream hashes | verified offline |
| Timeout and cancellation | Intent precedes verified TERM/KILL; TERM-resistant root and descendant disappear before completion; repeated terminal cancel is harmless | verified offline |

Verification at this checkpoint: 62 core tests, including fourteen supervisor
tests and seven independent supervisor regressions; 10 legacy shell files with
202 assertions; and a temporary wheel installation that applied migration 2
and imported the supervisor.

The next slice is durable start/status/events/result/cancel/resume service
behavior and a supervisor process that survives the launching shell. This
checkpoint does not claim those behaviors or a working engineering workflow.
