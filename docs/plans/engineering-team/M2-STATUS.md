# M2 implementation status

M2 is **in progress**. The store checkpoint is `0c2929c`; the bounded
supervisor lifecycle checkpoints are `0ee4cc0` and `df955f4`; the first
service checkpoint is `a0794a9`.

| Store requirement | Evidence | Status |
|---|---|---|
| SQLite WAL and packaged schema migration | Fresh install opens the packaged migration; four independent processes concurrently initialize one database | verified offline |
| Canonical request idempotency before mutable snapshot resolution | Concurrent connections return one run for the same key/body; a changed body conflicts | verified offline |
| Canonical project identity | Main and linked Git worktrees resolve to the same absolute common directory and project ID | verified offline |
| Preparing-owner fencing, recovery and cancellation | Runs remain `queued` with a private `preparing` phase; explicit recovery rotates the fencing token and replays the immutable submitted request; competing reclaimers yield one owner; stale completion after cancel/reclaim conflicts | verified offline |
| Transactional projections and events | Compare-and-swap run version and append-only event commit together under concurrent writers | verified offline |
| Atomic hash-verified artifacts | Content-addressed files finalize before reference; references increment run version with an event; duplicates and terminal mutation cannot clobber prior content | verified offline |
| Schema migration and future refusal | Source fixtures upgrade from versions 1 and 3; an installed wheel applies migration 004 from a schema-3 fixture; newer unsupported versions are rejected | verified offline |
| Supervisor and writer fencing | Transactional claims allow one supervisor and one active writer per worktree; ambiguous ownership retains the database fence | verified offline |
| Strong process identity and recovery | Darwin start second+microsecond identity is stable; live children remain owned without relaunch; dead and reused identities receive distinct recovery dispositions and reused IDs are never signalled | verified offline |
| Bounded process lifecycle | Direct argv runs in a new session; heartbeat, PID/PGID/start identity, token and package digest persist; stdout/stderr drain continuously with truncation and full-stream hashes | verified offline |
| Timeout and cancellation | Intent precedes verified TERM/KILL; TERM-resistant root and descendant disappear before completion; durable timeout remains failed when TERM exits 0; every queued/preparing/launching cancellation atomically publishes a receipt | partial: cross-process cancel/cleanup races remain |
| Durable gated launch | A persisted attempt runner and an inner worker gate prevent task execution before strong runner and child identity records; the runner owns timeout, cancel polling, bounded spool files, exact opened stdin and an fsynced exit receipt | verified offline |
| Coordinator-loss recovery | Preparing work is reclaimed under a rotated token; a real process crash after attempt reservation is fenced and requeued; a separate coordinator killed while the runner lives is not relaunched; two processes import one completed receipt atomically | partial: post-identity/pre-gate and orphan-child recovery remain |
| Frozen package | Every regular runtime asset is hashed, copied atomically, fsynced, stored on the run and verified before initial launch or resume; `-P` regressions prevent repository shadowing of internal modules | verified offline |
| Public workflow guard | Public branch-review tasks end with `CAPABILITY_UNAVAILABLE` until M3; only the private test argument can invoke the M2 fake step | verified offline |
| Result and event reads | Status is a transactional run/attempt snapshot; cursors always report the last consumed position and `has_more`; all terminal paths require a hash-checked result receipt | verified offline |
| Predecessor link | Public and fixture paths validate terminal same-project predecessors before mutable filesystem work; valid lineage survives later preparation failure while missing, active and foreign-project predecessors fail with a receipt | verified offline |
| CLI envelope and wait behavior | Exact v1 envelopes/exit codes, M2 operation dispatch, observation-only Ctrl-C, and terminal `start --wait` mappings | verified offline |

Checkpoint `f9f2ffa` passes 94 core tests and 202 shell assertions. The core
suite includes an installed-wheel schema-3-to-4 migration, two-process receipt
import and preparation-claim races, real crashes after reservation,
repository-retarget/shadow resistance, exact durable stdin, and a timeout whose
TERM handler exits zero. Detached supervisors are reaped without the earlier
`ResourceWarning`s. An independent review found project-identity and lineage
defects in the first preparation-recovery patch; both have regressions and were
fixed before this checkpoint.

Remaining acceptance work is post-identity/pre-gate and orphan-child
reconciliation, cross-process cancel/start/writer races, host-handoff storage
and claim fencing, and a final independent M2 gate. This is not a claim of a
working engineering workflow; branch-review execution begins in M3.
