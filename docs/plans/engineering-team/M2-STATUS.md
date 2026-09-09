# M2 implementation status

M2 is **in progress**. The store checkpoint is `0c2929c`; the bounded
supervisor lifecycle checkpoints are `0ee4cc0` and `df955f4`; the first
service checkpoint is `a0794a9`.

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
| Durable gated launch | A persisted attempt runner and an inner worker gate prevent task execution before strong runner and child identity records; the runner owns timeout, cancel polling, bounded spool files and an fsynced exit receipt | verified offline |
| Coordinator-loss recovery | A separate coordinator is killed while the runner lives; resume does not relaunch it, and a completed receipt is imported once with one attempt | verified offline |
| Frozen package | Every regular runtime asset is hashed, copied atomically, fsynced, stored on the run and verified before initial launch or resume | verified offline |
| Public workflow guard | Public branch-review tasks end with `CAPABILITY_UNAVAILABLE` until M3; only the private test argument can invoke the M2 fake step | verified offline |
| Result and event reads | Status is a transactional run/attempt snapshot; cursors always report the last consumed position and `has_more`; terminal service results verify referenced blob hashes and require a durable receipt for attempted runs | verified offline |
| Predecessor link | A superseded run must be terminal and belong to the same canonical Git project; the link is committed with preparation | verified offline |

The recovery checkpoint `a0794a9` passed 67 core tests and 202 shell
assertions. The current service candidate passes 69 core tests, including a
coordinator-crash receipt-import case. Final shell and wheel evidence will be
recorded at the acceptance checkpoint.

Remaining acceptance work is the independent service adversarial gate,
cross-process race expansion, CLI envelope verification and installed-wheel
migration 3 check. This is not a claim of a working engineering workflow;
branch-review execution begins in M3.
