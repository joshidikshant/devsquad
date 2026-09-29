# Sol execution plan and review feedback — September 29, 2026

## Assignment and starting point

Continue the existing DevSquad build on `codex/engineering-team`. Repair the
review findings below, finish the missing runtime connections, and prove the
requested product through the public service and installed normal commands.
This is a continuation of [SOL-HANDOFF.md](SOL-HANDOFF.md), not a redesign.
The full delivery remains M1–M7 plus C1; the immediate engineering priority is
the shared acceptance repair followed by M5–M7. C1 is optional to invoke but
required to implement under the original assignment.

Review baseline: `f4fa6577e2e891151231c9c7d3180be6e9e23faa`. Compare Git state
before acting and retain later work. The review made no source changes.
Installed release at review:
`0.1.0-py31214-9e5cdea2aa99-mcp-a26bc88afbef`, with no detected payload drift.

Read `AGENTS.md`, `CONTRIBUTING.md`, [RESUME.md](RESUME.md), this document and
[backlog.json](backlog.json) first. Load the relevant contract/specification
for each work package. Do not reload the historical chat or restart M1/M2.
This review supersedes the earlier claim that only credentials remain.

Current execution checkpoint: **R1 source repair verified**, with public
mutation regressions, a ten-case workspace mutation matrix, historical replay
coverage and 330-test core gate (two optional-SDK skips). See
[R1 evidence](evidence/R1-candidate-integrity-2026-09-29.json). Start at **R2**
after checking current Git state. R2–R8 and installed refresh remain open.

## Feedback on what has been built

Preserve the substantial working implementation: the durable runner and
ledger, process ownership and recovery, isolated worktrees, fenced host
handoffs, Codex protocol repairs, immutable installer and MCP integrations.
The successful installed Codex review receipt is real and remains valid for
that exact run. Its SHA256 was rechecked:
`0103db19a528cff916ebef80c6ec94b682ed4801fc22db020cdbc21796040f70`.

The main problem is the strength of completion claims. Several components
were marked complete because their unit fixtures passed, even though the
normal command path bypasses them or cannot produce their input evidence.
Passing counts do not demonstrate that the complete workflow enforces its
contract. Each new completion claim needs an actual runtime caller, a public
path regression and the corresponding installed/live proof where required.

During this review, the complete Python suite ran 317 tests successfully
(two optional-SDK skips), and all 227 Bash assertions passed. The generated
reference and installed-source comparison passed. A recurring unclosed
SQLite `ResourceWarning` appeared despite process exit zero; it remains an
open cleanup issue. Initial sandbox-restricted runs were interrupted because
process inspection was denied; the successful reruns had the process access
needed by the cancellation tests. Do not count the interrupted runs as passes.

### Findings and evidence limits

Source locations refer to the baseline above and will move as repairs land.

| ID | Priority / affected scope | Observed behavior and evidence | Repair package |
|---|---|---|---|
| F1 | P1 / M3, M5 | `review_worker.py:169` never revalidates candidate source after checks. A real-Git direct worker/evaluator reproduction froze `VALUE='wrong'`, changed it to `fixed` in a required check, then returned `passed` and `accept_allowed:true` while the frozen candidate stayed wrong. The full service reproduction was environment-blocked, so terminal service acceptance is not claimed. Violates CONTRACTS §5. | R1 |
| F2 | P1 / M5 | `claude_delivery_worker.py:256` copies requested model/effort into observed identity and labels it verified. A mocked result reporting `different-model` still yielded requested-model/low/verified; absent model identity also passes. `workflows.py:572` checks only that identity is a dictionary. | R2 |
| F3 | P1 / M6 | `learning.py:396–414` checks unique case IDs but permits reuse of outcome IDs. A confirmed evaluator reproduction reused two outcomes across two evaluation cases and one held-out case and returned `promotion_proposal`. Qualification reads those inflated counts. | R3 |
| F4 | P1 / M6, M7 | `task_entry.py:136` selects the provider default or first model; `_managed_routing` uses singleton concrete profiles and empty bindings. `store.py:2588` skips lifecycle overlay without aliases. Catalog default changes can change normal routing without qualification. | R4 |
| G1 | Required integration / M6 | The only production read of `experiment_assignment` is in `store.py:1522`; there is no writer. Public runs cannot supply the experimental outcomes required by the evaluator. `record_outcome` is reached through manual `outcome_add`, not ordinary terminal completion. | R5 |
| G2 | Required integration / M6 | Last-good catalog persistence has no production caller. Normal discovery has no scoped persistent refresh/cache path. Native Codex quota observations are not ingested; observations require manual input. | R4 |
| G3 | Required usability / M7 | Normal tasks always use `lead.mode=host`; terminal completion requires manual claim/decision JSON. `status`/`result` require an ID. Doctor checks binaries/registrations, not provider authentication. All CLI responses are JSON even without `--json`. | R6 |
| G4 | Verification coverage / M5, M7 | Default check detection on DevSquad selects only `bash test/run.sh`; the Python core suite is omitted, including when the requested fix changes Python core code. Detection also inspects the current checkout rather than the selected target tree. | R6 |
| G5 | Remaining full-delivery scope / C1 | `squad council` is absent. Its implementation and acceptance are included in the full assignment. | R7 |

The optional decision helper currently executes only a fake adapter;
non-fixture requests record `unavailable`. This is consistent with the narrow
M6-D1 contract, not proof of a production Jev integration. The one-request
Jev pilot is separate from runtime adoption. Do not enable a classifier or
claim routing improvement from a synthetic smoke result.

## Work order and ownership

R1, R2 and R3 come first. R4 precedes R5; R6 integrates the repaired public
paths. R7 follows the relevant M3–M6 repairs. R8 proves the installed product;
its authentication and classifier subgates may remain externally blocked.
Continue all independent work when a live subgate is blocked.

Use one coordinator and small commits. A bounded independent review may run
alongside non-overlapping work, but do not recursively spawn agents or run
multiple full suites concurrently. `service.py`, `store.py`, `workflows.py`
and the ledger documents require a single integration owner. Prefer focused
offline regressions before a full gate and one justified live probe.

### R1 — Preserve candidate identity through trusted checks

**Files:** `review_worker.py`, `workspaces.py`, `workflows.py`, and review/
delivery runtime tests. Shared M3/M5 fix; no provider dependency.

1. Turn F1 into a regression through the public service for both review and
   delivery. Retain the exact pre-check candidate, check output and mutation.
2. Revalidate HEAD/index/tracked content and candidate inputs across each check
   boundary. A tracked edit, deletion, mode change or checkout cannot provide
   passing evidence for the original candidate or contaminate the next check.
   Distinguish ordinary permitted build outputs from candidate source changes.
3. Make the integrity failure visible in evaluation, handoff and terminal
   reports; both host and headless disposition must refuse acceptance.

**Acceptance:** source-changing check cannot accept; a second check cannot
validate an unnoticed changed tree; ordinary non-source build artifacts and
clean checks work; the original checkout is unchanged; recovery/replay cannot
restore invalidated evidence. Preserve per-check temporary HOME behavior.

### R2 — Observe and validate Claude execution identity

**Files:** `claude_delivery_worker.py`, `workflows.py`, adapter fixtures and
delivery tests. Implement conformance offline before spending a live turn.

Investigation checkpoint (September 29): installed Claude `2.1.220` exposes
`session_id` and `modelUsage` in native result JSON. Preserve all reported model
entries; `canonicalModel` is pricing normalization, not serving-model proof.
The result does not report effective effort, so keep it unknown rather than
copying `--effort`. Multiple entries do not identify a unique writer. Pin the
parser to tested local capability; current online docs can describe newer CLI
features. Primary references: [result schema](https://code.claude.com/docs/en/agent-sdk/python#resultmessage),
[model aliases and effort limits](https://code.claude.com/docs/en/model-config),
and [usage attribution](https://code.claude.com/docs/en/agent-sdk/cost-tracking).
Do not replace subscription-compatible safe mode with `--bare`.

1. Verify the supported CLI's native model/session/usage fields against its
   installed schema/help and current official documentation when necessary.
   Record requested settings separately from actual reported identity.
2. Parse effective model identity, including alias resolution and multiple
   model entries where the CLI reports them. Preserve unavailable effort or
   backing revision as unknown. Never manufacture an observed setting from
   the requested argv.
3. Validate imported identity and enforce the existing independent-review
   contract. Missing, contradictory or insufficient identity evidence must
   not qualify as verified independence. Keep failed evidence in the receipt.

**Acceptance:** matching exact identity, alias-to-effective model, unexpected
model, absent identity, multiple models, unavailable effort and tampered
evidence all have explicit tested results. The genuine two-harness live gate
remains open until the corrected adapter obtains a real receipt.

### R3 — Make experiment and held-out evidence independent

**Files:** `learning.py`, experiment/qualification paths in `store.py`, and
learning/lifecycle tests. No key or provider required.

1. Reject duplicate outcomes across cases/arms/splits. Persist enough frozen
   case, run and split provenance to prevent the same underlying observation
   being relabelled to inflate sample size or contaminate held-out evidence.
2. Validate that each arm belongs to its declared project, experiment,
   concrete profile fingerprint and paired input contract. Review arms require
   the same candidate; implementation arms require the same baseline, task
   and checks. An experimental label alone is insufficient to establish a
   controlled one-variable comparison.
3. Enforce these checks before evaluation persistence and qualification.
   Audit any saved qualifying evaluations affected by the bug; preserve them
   as historical evidence and prevent unsafe future use rather than deleting
   or silently rewriting receipts. Late escaped-defect corrections must
   trigger an explicit evaluation/qualification revision or review policy,
   not leave stale success silently eligible for future promotion.

**Acceptance:** the two-outcome/three-case reproduction is rejected; repeated
run evidence, crossed split membership, wrong-profile arms and mismatched
tasks fail; valid disjoint pairs qualify; failures/missingness remain visible;
replay cannot bypass the repaired gate. Promotion and rollback use the same
validated evidence path.

### R4 — Connect normal routing, catalog lifecycle and quota

**Files:** `task_entry.py`, `catalog.py`, `capacity.py`, `router.py`,
`service.py`, `store.py`, adapter metadata and relevant tests.

1. Resolve ordinary roles through approved stable aliases and existing
   policy. Preserve explicit concrete pins and qualified fallbacks. Bootstrap
   without evidence must remain an explicitly labelled bounded trial and
   cannot become a proven default through catalog ordering or provider hints.
2. Connect discovery to account/config/version-scoped last-good snapshots,
   TTL, a refresh lease, bounded timeout/backoff and pagination. Wire complete
   drift into affected-profile revalidation and qualified fallback/block.
   Incomplete discovery must preserve the incumbent and prior snapshot.
3. Normalize documented native Codex quota observations into existing typed
   pools/windows, with timestamps, confidence and unknown values preserved.
   Use the existing reservation fence for launch decisions and keep manual
   observations available for unsupported providers.

**Acceptance:** changed provider default does not change an approved alias;
approved promotion affects new runs only; exact pins and old runs stay fixed;
partial/auth-failed discovery does not remove models; two projects honor
fresh weekly exhaustion despite short-window availability; stale/unsupported
quota remains unknown; no permission or billing expansion occurs. Exercise
these through normal task entry, not only router/store helper calls.

### R5 — Feed learning and trials from actual saved runs

**Files:** terminal transition/report paths, `service.py`, `store.py`,
`learning.py`, `lifecycle.py`, migrations if needed, and public service tests.

1. Project objective terminal facts into an idempotent final outcome record,
   including failed attempts, repairs, disposition and evidence references.
   Keep subjective later corrections explicit. Recover safely from a crash
   between terminalization and outcome projection without duplicate records.
2. Freeze the experiment, cases, splits, profiles and budgets before either
   arm launches, and run bounded paired
   trials through the existing runner, account reservations and experiment
   budgets. Derive consumed budget from durable attempts, not imported counters.
   Carry assignment, case/split and profile provenance into outcomes. Use a
   thin bounded controller, not a new general-purpose scheduler.
3. Connect public reporting, evaluation, qualification and proposal generation
   to those outcomes. Trace and fix the SQLite cleanup warning without merely
   suppressing it.

**Acceptance:** a public offline run completes and appears in `report` without
manual outcome import; real public fixture runs yield evaluator-eligible
paired evidence without direct SQL state fabrication; a failed original
attempt repaired later is not credited as independently successful; late
correction, crash/replay, budgets and promotion/rollback all remain truthful.
Exercise the complete public chain: freeze experiment → run both arms → record
terminal outcomes → evaluate → qualify → promote for a new run → rerun held-out
cases → roll back. Count independent completed pairs, not labels or retries.

### R6 — Finish the normal terminal and readiness experience

**Files:** `cli.py`, `task_entry.py`, `diagnostics.py`, integrations, runtime
guide and public CLI tests. Use the existing service and single lead authority.

1. Provide a supported terminal path from normal review/fix entry through
   disposition without hand-written JSON, using the configured existing
   headless lead or an explicit guided host path. Keep low-level JSON APIs.
   Resolve omitted status/result IDs only for an unambiguous current-project
   run; otherwise show choices. Supply readable output and a real next action.
2. Separate installed/registered/supported/authenticated/operation-verified
   readiness. Use non-generating auth checks where supported and report
   unknown otherwise. Missing auth must not advertise an unavailable workflow
   as ready. Keep required logins in the user's normal provider flow.
3. Discover checks from the selected committed target and approved project
   contracts. For DevSquad, include the Bash suite, relevant Python core suite
   and generated-reference check. Do not guess unittest from a directory
   named `tests` in an arbitrary non-Python project. Keep scope/check approval
   bounded and do not let generated preparation expand permissions.

**Acceptance:** fresh temporary install plus normal CLI review/fix reaches an
offline terminal receipt without manually authored task/decision JSON; zero,
one and multiple current-project runs behave correctly; unrelated runs are
never selected; Claude logged out is visible; target-ref check discovery is
stable; a Python defect cannot pass the DevSquad default delivery gate through
the Bash-only path. Update the quickstart to the verified commands.

### R7 — Complete C1 within the existing runner

Read [SELECTION-AND-COUNCIL.md](SELECTION-AND-COUNCIL.md) before this package.
It is separate from M7; its offline implementation can proceed while live
authentication gates wait. No new service, dashboard or multiple writers.

1. Add the gated workflow/schema and `squad council` normal entry with two
   distinct verified proposer identities, a critic distinct from both, and
   the existing sole lead. Apply shared profile/capacity/budget rules.
2. Enforce independent proposals through stage barriers and artifact access,
   then structured critique with validated labels/order and retained dissent.
   Persist every attempt and support cancellation, recovery and missing quorum.
3. Run adversarial offline fixtures and the predeclared matched/held-out
   comparison; obtain the required bounded subscription live receipt when
   eligible identities are available. Keep automatic triggering disabled
   unless its separate evidence and reviewed policy authorize it.

**Acceptance:** no peer draft access or self-scoring; missing/invalid critic
cannot become consensus; label shuffling is correctly reversed; a wrong
majority cannot override failed checks; budgets and restart preserve history.
Inconclusive comparison is recorded as inconclusive, with automatic use off.

### R8 — Install, prove live gates and audit closure

1. Install the repaired immutable release and run the quickstart walkthrough,
   dependency/drift/idempotence checks and required installed-SDK tests.
   Independently review the repaired paths. Preserve previous releases and
   saved receipts; a new release does not erase earlier failures.
2. After normal Claude login, prove the M4 real-host handoff and one bounded
   installed Claude implementation → verified different-model Codex review →
   mandatory checks → disposition. After Grok login, prove its supported
   operation. Recheck affected Codex/Gemini surfaces when their paths change;
   unchanged historical receipts are not a reason for repeated model calls.
3. Handle the classifier gate under its existing authorization: if the key
   becomes available, run the prepared synthetic Jev request once, no retry,
   with a $0.01 maximum and verified current pricing. Run pinned local Laya
   only on the declared trigger. A larger hosted comparison needs separate
   authorization. Audit every requirement and report measured limitations.

**Acceptance:** requirement-to-evidence matrix contains exact revisions,
commands, outcomes, candidate/receipt hashes and verified scope. M5/M6/M7 and
C1 close only against their own required gates. External failures remain
explicit; keep-off is a valid classifier adoption outcome, not missing-proof
permission to claim routing improvement.

## Verification and delivery discipline

- First write a behavior regression that fails on the reviewed baseline,
  then implement the repair. Test public transitions, not only dictionary
  constructors or helper output. Keep fixture evidence distinct from live.
- Run relevant Python tests for each code slice and `bash test/run.sh` before
  every commit. Run complete Python and generated-reference gates at coherent
  integration boundaries. Do not repeat unaffected suites just to accumulate
  counts. Use the installed SDK environment to resolve relevant optional skips.
- Cancellation tests need process inspection. If a sandbox denies it, record
  that limitation and obtain the narrowly needed execution permission; do not
  weaken recovery logic to make sandbox-restricted tests appear green.
- Update the milestone's review item, evidence and `RESUME.md` per coherent
  checkpoint. Preserve historical receipts and report failures accurately.
  End a pause with a clean committed tree; never stash. No push/merge/deploy.
- Use existing subscription harnesses for bounded required live proofs.
  No credit purchase, reset redemption, paid API fallback or global AI-setting
  change is authorized. Credentials and raw provider output stay outside Git.
- Report remaining work as concrete acceptance gates, not invented completion
  percentages or fixed five-hour-window estimates. Shared quota depends on
  actual models, context and account usage.

## First action for Sol

R1's source repair is verified; continue at R2 with failing native-identity
and import-tampering regressions. Preserve R1's check-output contract and
mutation evidence. Continue R2–R6 without waiting on the Claude login or Jev key. Retain R7
and R8 in the full scope and continue their independent work as dependencies
become ready.
