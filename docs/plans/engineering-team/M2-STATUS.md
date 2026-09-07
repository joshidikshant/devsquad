# M2 implementation status

M2 is **in progress**. The first store checkpoint is `0c2929c`; process
supervision, recovery and public start/status/cancel/resume service behavior
have not started.

| Store requirement | Evidence | Status |
|---|---|---|
| SQLite WAL and packaged schema migration | Fresh install opens the packaged migration; four independent processes concurrently initialize one database | verified offline |
| Canonical request idempotency before mutable snapshot resolution | Concurrent connections return one run for the same key/body; a changed body conflicts | verified offline |
| Canonical project identity | Main and linked Git worktrees resolve to the same absolute common directory and project ID | verified offline |
| Preparing-owner fencing and cancellation | Runs remain `queued` with a private `preparing` phase; stale completion after cancel conflicts; generic events cannot bypass the fence | verified offline |
| Transactional projections and events | Compare-and-swap run version and append-only event commit together under concurrent writers | verified offline |
| Atomic hash-verified artifacts | Content-addressed files finalize before reference; references increment run version with an event; duplicates and terminal mutation cannot clobber prior content | verified offline |
| Unsupported future schema refusal | A database newer than migration version 1 is rejected | verified offline |

Verification at this checkpoint: 46 core tests, including six independent M2
review regressions; 10 legacy shell files with 202 assertions; and a temporary
wheel installation that applied the packaged migration.

The next slice is the M2 supervisor and recovery foundation: durable service
operations, one supervisor claim and one active writer, process identity,
bounded output, cancellation/reaping, and crash reconciliation. This checkpoint
does not claim those behaviors or a working engineering workflow.
