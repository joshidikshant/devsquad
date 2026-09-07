# M1 implementation status

M1 is implemented through candidate `a67ab58` and remains **in progress**
pending independent review and a successful integrated native live probe. M2
process ownership has not started.

| # | Requirement | Evidence | Status |
|---|---|---|---|
| 1 | Native framing, typed requests, handshake, lifecycle and correlation | Bounded buffered JSON-line peer; `initialize` then `initialized`; installed 0.135.0 request shapes; conforming fake server with interleaved notifications | verified offline |
| 2 | Truthful provider completion and malformed-frame handling | Codex requires correlated terminal `turn/completed`; provider-specific document/JSONL parsing; partial, startup-only and malformed fixtures | verified offline |
| 3 | Strict Task, Profile, Policy, LaunchSpec, identity and result inputs | Runtime validators, matching strict schemas, adversarial nested-type tests and independent reviewer regressions | verified offline |
| 4 | Model/version-scoped capability preparation | Complete paginated discovery feeds native preparation; explicit unknown model, effort and drift cases fail before launch | verified offline |
| 5 | Shared Bash/Python classification taxonomy | Packaged `classification-policy.conf` is consumed by both paths; auth-topic, ordering and empty-success regressions | verified offline |
| 6 | Portable watchdog ownership and cleanup | Forced portable Bash 3.2 tests cover fast return, descendant cleanup, and a ready TERM-ignoring root with an absolute fake executable | verified offline |
| 7 | Complete native pagination and last-good behavior | Empty pages, repeated cursors, malformed/disconnected pages and incomplete-refresh retention are exercised | verified offline |
| 8 | Requested and observed identity remain separate | `ExecutionIdentity`, `LaunchSpec` and `NormalizedResult` validation and round trips | verified offline |
| 9 | Execution, artifact and acceptance states remain separate | Normalized result contract and classifier tests | verified offline |
| 10 | Tracked, bounded Antigravity context | Literal Git inventory; ignored/binary/secret/oversize omissions; file and ancestor-symlink containment tests | verified offline |
| 11 | Package and CLI envelope | Temporary wheel installation resolves schemas, adapters and shared taxonomy; input/readiness exit behavior is tested | verified offline |
| 12 | Bounded real starting-profile smoke | Codex CLI `gpt-5.5`, low effort, read-only, ephemeral JSONL invocation completed on 2026-09-06 | verified live (CLI) |
| 13 | Integrated native preparation/protocol/classification probe | Correct handshake initializes, but the earlier inline app-server attempt stalled at `model/list` or `thread/start`; root is diagnosing with a saved probe | pending live |
| 14 | Independent gate review | Reviewer regressions are tracked and pass; final root disposition remains outstanding | pending final review |

The Python bridge returns launch/protocol preparation and normalized parser
policy only. It does not spawn a worker or implement a second watchdog. M2
remains the sole owner of process sessions, timeouts, cancellation, draining
and reaping for new runs.

Declared limitations at this checkpoint:

- Grok workspace-write preparation is unsupported; its verified profile is
  read-only.
- Antigravity and Grok inference were not used for the live M1 smoke.
- Policy learning, routing semantics and session supervision remain deferred
  to their specified later milestones.
