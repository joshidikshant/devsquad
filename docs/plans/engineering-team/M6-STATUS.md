# M6 implementation status

M6 is **in progress**. Shared capacity, the append-only outcome ledger,
comparison reports, replay-safe one-variable experiment evaluation, proposal
generation, the complete offline profile lifecycle and the default-off typed
decision helper are verified. Only the externally blocked Jev measurement and
its conditional Laya follow-up remain open.

| Requirement | Planned evidence | Status |
|---|---|---|
| Strict capacity evidence | Typed pool/window/scope/source/confidence/TTL validation; stale, estimated and incomplete measurements remain unknown | verified at `21b1331` |
| Shared transactional reservations | Two projects share one pool; one-slot races produce one owner; schema-8 active attempts survive migration; ambiguous ownership retains the reservation | verified at `26ce5cf` |
| Capacity-aware routing | All applicable windows and profile sublimits affect deterministic selection; a later exhausted observation blocks reservation; paid API remains policy-gated | verified at `d170c01` |
| Public observation/status surface | `squad capacity observe --file FILE`; replay-safe persistence; status shows frozen and current detailed evidence | verified at `d170c01` |
| Final and late outcomes | Preserve attempt contribution, lead repair, final success and escaped-defect corrections without crediting failed attempts | verified at `d621df2` |
| Comparison reports | Sample sizes, missingness and separated automatic/pinned/experimental evidence | verified at `45ebc9e` |
| Frozen experiment evaluation | One-variable paired evaluation/held-out cases, failure evidence, no-change or promotion-proposal verdict and rollback target; evaluation never changes active policy | verified at `edfb3f3` |
| Draft proposals | `learn propose` emits content-addressed JSON/Markdown with hashes, sample sizes, missingness, failures and rollback; no evidence yields no-change | verified at `98c6685` |
| Held-out rerun and rollback | Post-change held-out evidence and exercised rollback through lifecycle bindings | verified at `4b27e0c` |
| Model lifecycle | Templates, qualification budgets, reviewed/guarded-auto promotion, compare-and-swap bindings, new-run-only effects and rollback receipts | verified through `ca4ee73` |
| Catalog drift and unavailable incumbent | Complete catalog drift scopes revalidation; added models stay unqualified; removed incumbents roll back only to a prior proven/qualified binding or block | verified at `398ae6a` / `ca4ee73` |
| Decision helper M6-D1 | Default-off typed contract, fake adapter, cache/accounting and authority/integrity tests | verified at `87fa9cf` |
| Jev M6-D2 | One capped synthetic request with exact model/usage/latency/cost receipt | blocked on `TYPESAFE_API_KEY` |
| Laya M6-D3 | Triggered pinned local comparison and measured keep-off/adopt decision | pending; run only if the declared Jev trigger fires |

## Capacity checkpoint

Schema 9 stores immutable capacity observations and explicit reservations.
Each observation retains its native unit and applicability scope; the latest
observation per scoped window is evaluated without inventing quota. A fresh
authoritative exhausted window wins over shorter available windows. Stale,
estimated, incomplete or absent evidence remains `unknown`, and `allow_bounded`
permits only one unresolved reservation.

Reservations use the shared SQLite write fence across projects. Attempt
reservations are created atomically with the attempt and reconciled by the same
transaction that proves ownership released; `ownership_ambiguous` remains in
flight. Migration backfills active schema-8 attempts so an update cannot create
a duplicate allowance.

Public preflight freezes detailed pool and per-profile evidence. Reservation
rederives current observations under the transaction, so evidence that changes
after preflight cannot launch against an exhausted pool. Profile/model-family
sublimits exclude only applicable candidates. The public CLI records strict,
replay-safe JSON, and run status returns both frozen and current windows.

The checkpoint gate is **255 core tests with 2 optional-SDK skips** and
ResourceWarning promoted to error, plus **220/220 Bash assertions**.

## Outcome and experiment checkpoint

Schema 10 adds append-only final and late-correction outcomes bound to saved
runs and attempts. A repaired task can succeed without falsely crediting the
failed original attempt, while a later escaped defect remains attached to the
original final verdict. `squad report --project PATH` reports sample size,
missingness and explicit contribution credit separately for automatic,
pinned and experimental selections.

Schema 11 adds immutable experiment specifications and replay-safe evaluation.
`squad policy evaluate --experiment FILE` compares declared control/candidate
pairs across evaluation and held-out splits, records missing and failed cases,
enforces non-inferiority/gain/escaped-defect gates and preserves an explicit
rollback version. Its output is only `no_change` or `promotion_proposal` and
always records `active_policy_changed: false`; reusing an experiment ID with a
different specification conflicts.

The combined checkpoint gate is **263 core tests with 2 optional-SDK skips**
and ResourceWarning promoted to error, plus **220/220 Bash assertions**.

`98c6685` adds `squad learn propose --project PATH`. It reads one consistent
ledger snapshot, verifies the latest experiment and evaluation hashes, and
writes local content-addressed JSON and Markdown drafts under the runtime
directory. The draft includes selection-mode sample sizes, missingness, all
recorded evaluation failures and the rollback version. Absent experiment
evidence produces an explicit `no_change`; even qualifying evidence produces
only `promotion_proposal`, with `active_policy_changed: false`. The gate is
**265 core tests with 2 optional-SDK skips** plus **220/220 Bash assertions**.

## Profile lifecycle checkpoint

Schema 12 persists strict versioned templates, immutable concrete profiles,
bounded qualification runs, compare-and-swap alias bindings, binding history
and decision receipts. Reviewed and opt-in `guarded_auto` promotion require
saved evaluation and held-out evidence. Guarded changes cannot alter policy,
permissions, billing, account route or expand tools. Binding changes affect
new runs only, while an exact concrete pin and an already frozen run do not
float.

`4b27e0c` requires a saved post-change `no_change` experiment before ordinary
regression rollback. The experiment must compare the active profile with the
requested predecessor, and its hash, failures, metrics and reasons are retained
in the decision receipt. `398ae6a` records complete catalog drift without
changing a binding: only profiles using changed or removed model IDs are
affected, same-ID drift remains explicitly uncertain, and added models remain
unqualified.

`ca4ee73` adds `squad profile binding-fallback --file FILE`. Complete,
profile-scoped removal evidence may roll an unavailable incumbent back to the
newest prior profile that is proven and, when applicable, backed by a still-
qualified run under the same lifecycle template. Removed, suspended, trial,
unqualified or authority-widening predecessors are skipped. If no safe
predecessor exists the transaction fails without changing the binding. The
catalog evidence and its hash are retained in the immutable rollback receipt.

The combined checkpoint gate is **275 core tests with 2 optional-SDK skips**
and ResourceWarning promoted to error, plus **220/220 Bash assertions**.

## Decision-helper checkpoint

`e1afbc6` adds a strict optional policy and typed observation/response contract.
Omitting the policy or selecting `off` leaves routing unchanged and creates no
call. `shadow` saves a suggestion without changing execution. `advisory`
requires reviewed gate evidence and can only reorder the exact profiles already
accepted by deterministic permission, billing, capability, identity, quality
and capacity filters. Pins cannot move. Unknown IDs, non-finite scores,
distribution errors, adapter/language drift, truncation, lateness, abstention
and insufficient confidence all preserve deterministic routing.

Schema 13 at `7e83b3a` stores content-addressed decision requests, per-run
links, response/usage evidence and an explicit billable-call count. The launch
fence is written before an adapter boundary. Resume reuses a completed result;
a call launched before a crash becomes `indeterminate` and cannot be silently
retried. Cancellation before launch records zero calls, invalid output is not
persisted as trusted data, and raw task content is represented only by hashes
and byte counts.

`87fa9cf` integrates the helper into public preflight and status. The frozen
snapshot records the observation and any advisory effect before worker adapter
selection. Missing optional adapters record `unavailable`, zero calls and the
unchanged route. The deterministic fake adapter proves cache reuse across runs,
shadow equivalence and advisory ordering. The frozen
`decision-helper-baseline-v1.json` links only the synthetic public corpus and
explicitly does not authorize advisory adoption. The gate is **291 core tests
with 2 optional-SDK skips** and **220/220 Bash assertions**.

## Exact next slice

Keep the one-request Jev M6-D2 gate blocked until `TYPESAFE_API_KEY` is
supplied. Do not install or run Laya unless the predeclared Jev access,
cost/usage or measured-quality trigger fires. Continue independent delivery at
M7 packaging, installation, update safety and real-surface usability.
