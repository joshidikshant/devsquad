# M3 implementation status

M3 is **complete** at implementation checkpoint `1737667`. The final gate
passes 188 core tests with `ResourceWarning` promoted to an error and all 10
Bash regression files/202 assertions. The bounded live Codex review evidence
remains the successful subscription-backed run recorded at `9478796`; no
additional provider turn was used for the closeout.

| Requirement | Evidence | Status |
|---|---|---|
| Deterministic selection | Versioned profile aliases, exact pins, explicit `none`/`policy` fallback, permission/billing filters and typed capacity produce stable frozen routing snapshots | verified offline |
| Frozen branch input | Base/target refs resolve to exact OIDs; committed config hashes, candidate hash and detached review/check workspaces remain stable while the submitted checkout, index and HEAD are preserved | verified offline |
| Reviewer evidence | Strict ordinary/adversarial prompts, review/check/evaluation schemas, candidate binding, read-only identity checks and malformed/denied/disconnected output faults prevent unsupported success | verified offline |
| Check and lead gates | Report-only failure stays visible without blocking acceptance; required failure blocks acceptance; host and headless leads support accept/reject/revise with bounded revisions | verified offline |
| Durable host handoff | Waiting JSON/Markdown packets, claim leases, renewal/takeover, stale completion fencing, replay and crash-resume continuation share the saved run ledger | verified offline |
| Terminal reporting | Success, rejection, preflight failure, worker failure, cancellation, waiting cancellation, timeout and budget exhaustion publish receipt JSON/Markdown, events, manifest and result receipt | verified offline |
| Headless leadership | Offline and native headless leads run as separate fenced attempts, verify their own frozen identity/evidence and terminalize without host intervention | verified offline |
| Runtime fallback | Reviewer and lead failures advance only through the frozen qualified fallback order; `fallback:none`, invocation/wall budgets and recovery-before-launch are enforced without profile substitution | verified offline |
| Capacity and accounting | Reservations are transactional, unknown pools permit one unresolved trial, live shared-pool reservations are observed, adapter launches are distinct from native usage and unavailable counts remain null | verified offline |
| Live branch review | Bundled Codex 0.153.4, gpt-5.5/low, ephemeral read-only execution found one supported regression, passed the required check and produced all five terminal report hashes | verified live |

## Acceptance mapping

- The live public branch review is bound to recorded base, target and candidate
  hashes and produced an actionable supported finding.
- Offline gates separately prove a supported clean verdict, report-only and
  required-check behavior, moving-ref stability, denied/missing output faults,
  two-host fencing and source-checkout preservation.
- Receipts retain the effective profile/model/effort/toolbox snapshot, every
  executed fallback profile, earlier review revisions and lead dispositions.
- `max_worker_invocations` counts durable runner launches, not reservations
  abandoned before their launch fence. Revision, fallback and wall budgets all
  end in explicit terminal reports rather than stranded resumable states.
- Standard and adversarial review prompts are distinct while sharing the same
  read-only evidence and candidate-binding rules.

## Independent audit closure

The bounded M3 audit at `84deb77` found four defects: a recovered pre-launch
reservation consumed a fallback slot, headless-lead budget exhaustion could
strand a run, unknown capacity allowed more than one unresolved trial, and a
later failed/cancelled revision omitted earlier attempts and dispositions.
`1737667` fixes all four and adds direct reproductions. The complete 188-test
gate and 202 Bash assertions pass after those fixes. The requested follow-up
agent re-run could not start because its shared Plus window was exhausted; the
finding-specific regressions and full local gates are the closure evidence.

## Boundary

M3 proves a useful saved branch review from terminal/service APIs. It does not
claim M4 local-app MCP access, M5 issue implementation, M6 lifecycle learning,
M7 installation/all-surface receipts or C1 Council. Those remain required by
the full assignment.
