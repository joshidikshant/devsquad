# Sol execution plan and review feedback — October 1, 2026

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

Current execution checkpoint: **R1 and R2 source/offline repairs verified**.
The implementation baseline is `ef988897e2dda495482dd26e7c583bc428086ba0`;
preserve any later commits. R2's final complete run passed 367 tests with two
optional-SDK skips in 215.886 seconds. The SQLite finalizer warning remains
open under R5; this is not a warning-free or installed/live pass. See
[R1 evidence](evidence/R1-candidate-integrity-2026-09-29.json) and
[R2 evidence](evidence/R2-observed-identity-2026-10-01.json). Start at **R3**.
R3–R8, C1 and affected installed/live proofs remain open.

### Ready-to-execute handoff for Sol

Do not reimplement R2. It now has a shared strict native parser, v2 imported
evidence, actual-attempt profile binding, actual-model independence and durable
failure/fallback/cancellation/history handling. The original 16 worker tests,
12 import/parser tests and nine public identity tests cover this repair.
Two independent-review findings were reproduced and repaired: another allowed
fallback profile cannot impersonate the actual attempt, and invalid UTF-8 or
CRLF native output retains its exact byte hash. The original
[red baseline](evidence/R2-identity-red-baseline-2026-09-29.json) remains history.

Preserve the verification history: a prior 367-test run failed six cases;
all six passed unchanged with stable clocks, and the final full run passed
with UTC and monotonic elapsed times agreeing. Do not erase the failed run or
weaken budgets to make tests pass. The current request is a planning handoff:
no additional implementation, installation or provider call was performed.

| Next slice | Deliverable | Gate before claiming completion |
|---|---|---|
| R1 / R2 | Preserve verified source repairs | Recheck affected regressions when shared code changes; installed/live proof remains R8 |
| R3a | Strict experiment provenance contract | Reused outcomes/runs/splits and mismatched paired inputs rejected |
| R3b | Durable validation and current-evidence eligibility | Replay, qualification, promotion, rollback and catalog fallback cannot reuse stale/unverified evidence |
| R3c | Public regression and migration evidence | Valid independent pairs work; legacy receipts remain readable; late corrections block new unsafe decisions |
| R4 → R5 → R6 | Routing, learning and normal terminal integration | Public-run evidence through the full chain, then fresh-install usability proof |
| R7 / R8 | Council and installed/live closure | Each separately required acceptance gate; external blockers remain explicit |

Use the same spec-based loop for every slice: name the contract and public
entry point; reproduce the missing behavior; implement the smallest coherent
change; run focused tests; review the patch; record evidence and the exact
next action; checkpoint. Run the full integration gates at package boundaries,
not after every small edit. One integration owner controls shared files; use
bounded leaf reviews only when they can run alongside useful local work.

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

During the original September 29 review, the complete Python suite ran 317 tests successfully
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

R1 and R2 source repairs are done; R3 is the next dependency. R4 precedes R5; R6 integrates the repaired public
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

#### R2 implemented contract to preserve

The following requirements are implemented at `ef98889`, not a new to-do
list. Keep them enforced when R3–R6 touch shared import/disposition paths.

- **R2a — parser/worker:** define a shared strict identity contract rather than
  letting the worker and importer invent separate interpretations. Require a
  valid success envelope, bounded nonblank session identity and well-formed
  native usage. Reject non-object, duplicate-key and non-finite JSON without
  leaking an `AttributeError`. Keep the frozen requested profile unchanged.
  A sole concrete `modelUsage` key may establish the reported model; preserve
  all entries and reject ambiguous writer attribution. Tested family aliases
  (`sonnet`, `opus`, `haiku`) may resolve only to a reported concrete model of
  that family, without a hardcoded current version. Pricing `canonicalModel`
  must not replace native usage identity; a differing pricing-only value is
  allowed and is not a serving-identity conflict. An optional top-level
  `model` is not identity authority either, but a contradictory serving-model
  claim fails closed. Keep effective effort/backing revision
  null when unreported, and make the scope of verification explicit. The new
  worker test file covers this layer only; import and public runtime suites
  provide the additional coverage. Fixtures must contain valid native evidence.
- **R2b — import/acceptance:** validate the native evidence against the frozen
  adapter, selected profile (including a legitimate fallback), session and
  claimed observation. A dictionary or `verification="verified"` label is not
  proof. Add regressions for forged model/effort/session/provenance and missing
  native evidence. Enforce independence using verified reported identities,
  not just requested aliases or family labels. Exercise same effective model
  behind different profile names, unknown/ambiguous identity and a valid
  different-model case through both host and headless delivery. Candidate
  finalization/import and later disposition must not bypass these gates.
- **R2c — failures/recovery:** persist bounded, redacted identity diagnostics,
  valid reported model/usage data and artifact references for rejected attempts
  in the durable receipt; exception text alone is insufficient. Test failure,
  fallback, cancellation and recovery/replay with no duplicate writer or lost
  attempt. Historical terminal receipts remain readable and exact terminal
  replays remain idempotent, but old unverified evidence cannot authorize a
  new acceptance. Version evidence/contracts explicitly if needed. Preserve
  R1's non-overridable check-integrity gates throughout.

Run the preserved worker regression set with:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=plugin/core/src \
  python3 -m unittest discover -s test/core -p test_claude_identity.py -v
```

This command is now expected to pass. Its historical red result belongs to
`6848f11` and the saved baseline artifact. The added public tests, full core
gate and independent patch review have completed; do not substitute these
offline results for the separately blocked installed/live receipt.

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

#### R3 execution slices and review feedback

Source inspection at `ef98889` confirms a broader problem than duplicate IDs:
`learning.py:343` accepts profile IDs without fingerprints;
`store.py:1719` loads outcome payloads without run/snapshot/attempt provenance;
evaluation replay (`store.py:1696`) and qualification replay
(`store.py:1994`) return before current evidence validation. Fix the complete
trust path, not only the sample counter.

1. **R3a — contract and red tests.** Define a versioned frozen assignment:
   experiment/spec hash, project, case, split, arm, tested role, concrete
   profile hash and paired-input hash. Reject global outcome reuse, underlying
   run reuse and evaluation/held-out input overlap. Review arms must share
   the candidate; implementation arms must share baseline and normalized
   task/scope/acceptance/checks. Explicitly define which incidental paths and
   tested routing field are excluded from paired-input hashing; do not drop
   substantive inputs to obtain a match.
2. **R3b — saved evidence and shared eligibility.** Join outcomes to saved
   runs, project identity, frozen snapshots and actual role attempts. The
   assignment/profile must agree with actual execution, including fallbacks.
   Persist final-outcome and correction-chain hashes in a versioned evaluation.
   One shared current-evidence gate must protect qualification, evaluation/
   qualification replay, new promotion, regression rollback and unavailable-
   profile catalog fallback. A new correction invalidates old eligibility;
   require an explicit new evaluation/review, not silent receipt rewriting.
3. **R3c — compatibility and public proof.** Preserve historical v1 records
   and exact completed-decision replay without another binding mutation.
   Label unverified historical evidence ineligible for new decisions. Replace
   positive tests that SQL-terminalize empty runs with distinct saved runs,
   frozen assignments and bound actual attempts. Keep R5's public trial
   controller as a separate deliverable; R3 must not claim it already exists.

Use this regression order: two outcomes reused across three cases; cross-arm
and underlying-run reuse; wrong project/experiment/case/split; same profile ID
with a different fingerprint; wrong actual fallback; mismatched candidate or
baseline/task/checks; valid disjoint pairs; failed/missing arms; atomic
persistence and replay; legacy read versus new decision; late escaped defect
after evaluation and after qualification; valid promotion/rollback and stale
catalog-fallback rejection. Record independent pairs, not the number of labels.

R3 can use the existing run IDs, project Git common directory, request hash,
frozen task/config hashes, candidate/baseline identity, selected profiles and
attempt profile indices. Do not trust caller-supplied `experimental` labels
as proof. Failed and missing evidence must remain visible and ineligible for
unearned success credit. Keep schema/contract changes and migrations together.

### R4 — Connect normal routing, catalog lifecycle and quota

**Files:** `task_entry.py`, `catalog.py`, `capacity.py`, `router.py`,
`service.py`, `store.py`, adapter metadata and relevant tests.

1. Resolve ordinary roles through approved stable aliases and existing
   policy. Preserve explicit concrete pins, their pinned-selection provenance
   and qualified fallbacks. A normal `--model` or `--review-model` selection
   must not be reported as automatic merely because task preparation embedded
   it as a singleton profile. Bootstrap
   without evidence must remain an explicitly labelled bounded trial and
   cannot become a proven default through catalog ordering or provider hints.
2. Connect discovery to account/config/version-scoped last-good snapshots,
   TTL, a refresh lease, bounded timeout/backoff and existing protocol pagination.
   Reuse `codex_protocol.py` pagination rather than replacing it. Wire complete
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
The existing normal profiles are already labelled `trial`; the defect is
bypassing stable alias/qualification policy, not a missing trial label.

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
   unknown otherwise. An unverified CLI version must not count as supported
   adapter readiness. Missing auth must not advertise an unavailable workflow
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
- Keep one short recovery entry with the source revision, exact active test
  session/log if any, observed failure, next command and changed files. Resume
  from that entry rather than repeatedly loading the full conversation. Do
  not launch a second full suite while the first remains live. Distinguish
  account quota, tool timeout, runtime budget expiry and host clock movement;
  they are not interchangeable explanations for an interruption.

## First action for Sol

Read the recovery files and verify current Git state. Preserve `ef98889` and
later work. Begin **R3a** with the two-outcome/three-case failing regression and
the versioned provenance contract above, then finish R3b/R3c before R4.
Preserve R1's check-integrity gate and R2's actual-attempt/native-identity gate.
Continue R3–R6 without waiting on the Claude login or Jev key. Retain R7/R8
and C1 in the full scope. Report each slice as red baseline, verified offline,
installed proof, or externally blocked; never collapse those into one claim.

Suggested instruction to SOL: “Execute this plan from R3 on
`codex/engineering-team`, in small spec → failing public test → implementation
→ verification → review → checkpoint cycles. Do not redo R1/R2 or reload the
old chat. After each slice report the evidence, open gates and exact next
action. Keep the classifier off until its separate adoption gate passes.”
