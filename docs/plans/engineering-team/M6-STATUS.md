# M6 implementation status

M6 is **in progress**. Shared capacity observations, reservations and routing
are verified; outcomes, experiments, lifecycle promotion/rollback and the
default-off decision helper remain open.

| Requirement | Planned evidence | Status |
|---|---|---|
| Strict capacity evidence | Typed pool/window/scope/source/confidence/TTL validation; stale, estimated and incomplete measurements remain unknown | verified at `21b1331` |
| Shared transactional reservations | Two projects share one pool; one-slot races produce one owner; schema-8 active attempts survive migration; ambiguous ownership retains the reservation | verified at `26ce5cf` |
| Capacity-aware routing | All applicable windows and profile sublimits affect deterministic selection; a later exhausted observation blocks reservation; paid API remains policy-gated | verified at `d170c01` |
| Public observation/status surface | `squad capacity observe --file FILE`; replay-safe persistence; status shows frozen and current detailed evidence | verified at `d170c01` |
| Final and late outcomes | Preserve attempt contribution, lead repair, final success and escaped-defect corrections without crediting failed attempts | pending |
| Comparison reports and proposals | Sample sizes, missingness, selection mode, one-variable experiment, held-out rerun, no-change/promotion proposal and rollback target | pending |
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

## Exact next slice

Add the schema-10 outcome ledger and `learning.py`: strict final/late records,
append-only corrections, attempt/lead contribution attribution, selection-mode
separation and comparison reports with sample sizes and missingness. Prove that
a failed original attempt later repaired by another profile yields final task
success without crediting the failed attempt, and that a late escaped defect
updates history without erasing the original verdict.
