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
| Schema migration and future refusal | Source fixtures upgrade from versions 1 and 3; an installed wheel applies migration 004 from a schema-3 fixture; newer unsupported versions are rejected | verified offline |
| Supervisor and writer fencing | Transactional claims allow one supervisor and one active writer per worktree; ambiguous ownership retains the database fence | verified offline |
| Strong process identity and recovery | Darwin start second+microsecond identity is stable; live children remain owned without relaunch; dead and reused identities receive distinct recovery dispositions and reused IDs are never signalled | verified offline |
| Bounded process lifecycle | Direct argv runs in a new session; heartbeat, PID/PGID/start identity, token and package digest persist; stdout/stderr drain continuously with truncation and full-stream hashes | verified offline |
| Timeout and cancellation | Intent precedes verified TERM/KILL; TERM-resistant root and descendant disappear before completion; durable timeout remains failed when TERM exits 0 | partial: pre-attempt receipts and cross-process cancel cleanup remain |
| Durable gated launch | A persisted attempt runner and an inner worker gate prevent task execution before strong runner and child identity records; the runner owns timeout, cancel polling, bounded spool files and an fsynced exit receipt | verified offline |
| Coordinator-loss recovery | A separate coordinator is killed while the runner lives; two processes import one completed receipt atomically without relaunch or duplicate events | partial: preparing/launching and orphan-child recovery remain |
| Frozen package | Every regular runtime asset is hashed, copied atomically, fsynced, stored on the run and verified before initial launch or resume; `-P` regressions prevent repository shadowing of internal modules | verified offline |
| Public workflow guard | Public branch-review tasks end with `CAPABILITY_UNAVAILABLE` until M3; only the private test argument can invoke the M2 fake step | verified offline |
| Result and event reads | Status is a transactional run/attempt snapshot; cursors always report the last consumed position and `has_more`; attempted-run receipts and hashes are checked | partial: failed-preflight/pre-attempt cancellation receipts remain |
| Predecessor link | Fixture execution validates terminal same-project predecessors and stores the link | partial: public capability-failure path still bypasses validation |
| CLI envelope and wait behavior | Exact v1 envelopes/exit codes, M2 operation dispatch, observation-only Ctrl-C, and terminal `start --wait` mappings | verified offline |

Checkpoints `5aa2e74` and `f0d29a7` pass 82 core tests and 202 shell
assertions. The core suite includes an installed-wheel schema-3-to-4 migration,
a two-process receipt-import race, repository-shadow resistance, and a timeout
whose TERM handler exits zero. Several service tests still emit detached
subprocess `ResourceWarning`s; M2 is not accepted while those ownership and
recovery gaps remain.

Remaining acceptance work is crashed preparation/launch reconciliation,
receipts for every terminal path, predecessor/stdin/cancel invariants,
cross-process start/writer/crash races, host-handoff storage and claim fencing,
ResourceWarning cleanup, and a post-fix independent Astra gate. This is not a
claim of a working engineering workflow; branch-review execution begins in M3.
