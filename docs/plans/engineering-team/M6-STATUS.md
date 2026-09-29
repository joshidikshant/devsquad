# M6 implementation status

M6 is **in progress**. Shared capacity, the append-only outcome ledger,
comparison reports and replay-safe one-variable experiment evaluation are
verified. Draft proposal generation, lifecycle promotion/rollback and the
default-off decision helper remain open.

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
| Held-out rerun and rollback | Post-change held-out evidence and exercised rollback through lifecycle bindings | pending |
| Model lifecycle | Templates, qualification budgets, reviewed/guarded-auto promotion, compare-and-swap bindings, new-run-only effects and rollback receipts | pending |
| Decision helper M6-D1 | Default-off typed contract, fake adapter, cache/accounting and authority/integrity tests | in progress; synthetic Jev probe mechanics only |
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

## Exact next slice

Implement versioned profile lifecycle bindings, qualification and guarded
promotion/rollback: allowed templates, bounded trials, compare-and-swap
binding versions, new-run-only effects, qualified fallback and immutable
decision receipts. Then exercise a held-out rerun and rollback before wiring
the default-off decision helper.
