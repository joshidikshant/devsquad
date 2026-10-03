# M2 implementation status

M2 is **complete** at implementation checkpoint `ddb6f51`. The final gate
passes 121 core tests with `ResourceWarning` promoted to an error and all 10
Bash regression files/202 assertions. No provider invocation was used for the
M2 gate.

| Requirement | Evidence | Status |
|---|---|---|
| SQLite ledger, WAL and packaged migrations | Concurrent fresh-database initialization; source fixtures migrate from schemas 1, 3 and 4; a wheel installed outside the checkout applies all packaged migrations through schema 5 and rejects a future schema | verified offline |
| Canonical start idempotency and project identity | Independent processes return one run for identical requests and conflict for a changed body; linked Git worktrees share the canonical common-directory project ID | verified offline |
| Preparing-owner recovery | Interrupted preflight replays the immutable submitted request under a rotated fence; competing reclaimers yield one owner; stale owners and repository retargeting are rejected | verified offline |
| Transactional state, events and artifacts | Projection/version compare-and-swap and events share a transaction; artifacts are atomically finalized and hash checked before reference | verified offline |
| Supervisor and writer fencing | Database claims permit one active worktree writer; strong process identity distinguishes live, dead and ambiguous/reused processes | verified offline |
| Durable gated launch | The persisted runner cannot open the inner worker gate until runner identity and spool paths are committed; a crash after identity but before gate release requeues without executing the command | verified offline |
| Coordinator-loss recovery | A launching shell can exit while another process observes/imports the run; a live runner is not relaunched; completed receipts import exactly once across processes | verified offline |
| Orphan-child recovery | A dead runner with a live recorded child blocks relaunch; owned recovery cancellation signals only the verified child group and confirms absence before terminal state | verified offline |
| Interrupted recovery cancellation | A spawned canceller exits immediately after persisting `recovery_cleanup`; the next public cancel resumes cleanup, emits one terminal event/receipt and repeated cancel is harmless | verified offline |
| Timeout, cancellation and stdin | Exact regular-file stdin bytes reach the child; a timeout stays failed even when TERM exits zero; cancellation intent precedes bounded TERM/KILL and terminal state follows confirmed cleanup | verified offline |
| Cross-process races | Independent-process tests cover identical/conflicting starts, worktree writer claims, queued/running cancel, receipt import, host claims and handoff completion replay | verified offline |
| Host handoffs | Schema-5 packets, bounded claims, renewal/takeover, submission replay, late audit, awaiting-host cancellation and CLI/service operations are fenced and durable | verified offline |
| Lease authorization under contention | Real independent SQLite writer locks force renewal and completion to wait across expiry; authoritative time is sampled only after the write transaction is acquired | verified offline |
| CLI and durable reads | Exact v1 envelopes/exit mappings, `start --wait`, observation-only Ctrl-C, transactional status, stable cursors and hash-verified terminal results | verified offline |
| Public workflow boundary | Public branch review fails explicitly with `CAPABILITY_UNAVAILABLE`; only the private fake-step hook exercises M2 lifecycle | verified offline |

## Acceptance mapping

- Concurrent identical starts converge on one run; a different request under
  the same key conflicts.
- Event/projection mutations and terminal artifact references remain
  transactional under independent writers.
- Killing a coordinator or runner never creates a second writer. Ambiguous or
  reused process identities are not signalled.
- A crash after artifact finalization but before its database transaction
  creates no false completion; resume imports the same finalized bytes once.
- Cancellation survives caller loss and never records terminal cancellation
  before the verified child group is absent.
- Schema migration is exercised from the preceding schema through an installed
  wheel, and newer unsupported schemas are refused.
- A second process can inspect and operate on the same saved run and can claim
  a host handoff; stale, expired and lock-delayed completions cannot advance it.

## Independent review closure

The final bounded review found two P1 races and no additional defect in its
targeted scope: lease time was sampled before SQLite lock acquisition, and an
interruption after orphan-cancel intent could leave public cancel stuck. Both
were fixed at `ddb6f51` and have deterministic contention/process-crash
regressions in the 121-test gate.

## Boundary

M2 proves the durable execution substrate, not a working engineering workflow.
`branch-review` intentionally remains unavailable until M3 implements frozen
review workspaces, deterministic profile routing, reviewer/check/lead flow and
bound reports. M3 is the first user-usable product checkpoint.
