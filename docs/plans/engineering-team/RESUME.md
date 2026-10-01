# Resume DevSquad after an interruption

This file is the recovery entry point for a quota cutoff, interrupted task or new coding-agent session. Update it at each coherent checkpoint and before a long live probe. A pending milestone stays pending when its evidence is incomplete.

## Current position — October 1, 2026

### Review correction and next action

R3a is source/offline verified at `672e383`: global outcome-reuse rejection,
versioned profile/input/assignment contracts, separate corpus-versus-pair
fingerprints, schema-14 prelaunch assignment persistence under the preparation
fence, and strict normalized v2 chain evaluation. The original five reuse
regressions failed before the repair and now pass. The focused experiment,
learning and lifecycle gate currently passes 47 tests; nine targeted migration
checks, including installed-wheel upgrades, also passed. Bounded review found
omitted tested-role fallback policy and native execution identity; both now
have regressions and fixes, including predeclared per-arm execution hashes.
The independent two-test follow-up passed with no remaining concrete finding
in the fixes. The full unchanged-source gate ran **402 tests in 213.208 seconds,
OK with two optional-SDK skips**; UTC/monotonic wrapper timing agreed at
213.376 seconds. The known SQLite finalizer warning remains R5. All 227 Bash
assertions and the generated-reference check passed. See
[R3a evidence](evidence/R3a-provenance-contract-2026-10-01.json). No test is
still running; begin R3b without repeating this unchanged gate.

**R3 remains in progress.** The v2 saved-run outcome reader is not wired into
`Store.evaluate_learning_experiment` yet, so public v2 evaluation fails closed
rather than accepting outcome labels as provenance. R3b must join immutable
assignments, actual attempts and outcomes, then enforce one current-evidence
gate for replay/qualification/promotion/rollback/catalog fallback. Audit/read
compatibility for unsafe legacy v1 proposals and public realistic fixtures
remain R3c; the public paired-trial controller remains R5. Do not count current
legacy positive fixtures as proof of that integration. No live providers,
installation refresh or global settings changes occurred.

R2 source/offline repair is verified at `ef98889`: strict native result parsing,
v2 import evidence, actual-model independence and durable failed diagnostics.
The 16 original worker regressions, 12 import/parser regressions, 19 existing
delivery tests and nine public native delivery/fallback/cancellation/legacy
tests have passed focused runs. Independent review identified two additional
gaps (actual durable-attempt profile binding and exact native-byte retention);
both were reproduced, repaired and independently rechecked with four passing
targeted regressions and no additional actionable finding.

The first integrated gate before those last two fixes passed 363 tests with
two optional-SDK skips. The subsequent 367-test gate completed with **six
failures and two skips**, not a pass. Four failures show budget expiration or
15–17 minute UTC jumps during a 231-second monotonic suite. On October 1, all
six failed cases passed unchanged in 18.998 seconds; per-test UTC and monotonic
elapsed measurements agreed. This supports an environmental timing explanation,
but does not erase the failed gate. The final unchanged-source rerun completed
**367 tests in 215.886 seconds, OK with two optional-SDK skips**; its wrapper
measured 216.021 UTC seconds and 216.020 monotonic seconds. The SQLite cleanup
warning still appeared and remains assigned to R5. All 227 Bash assertions
and the generated-reference check passed. **R2 source/offline closure is
recorded; installed/live closure remains R8.** See
[R2 evidence](evidence/R2-observed-identity-2026-10-01.json). No gate is running.

No live provider call, installed refresh or global provider-setting change
occurred. The original red baseline is retained in
[R2 baseline evidence](evidence/R2-identity-red-baseline-2026-09-29.json).
The planning handoff was saved at `b3de5c6`; the persistent implementation
goal subsequently resumed R3a as recorded above. Do not redo R2 or retry
blocked authentication. The reproduced two-outcome/three-case reuse is now
rejected, but R3's full saved-run provenance and eligibility repair remains.

R1's source repair is verified after `399d93d`: the public regression first
reproduced four unsafe mutation paths (review/delivery × host/headless). The
repair adds check-boundary fingerprints, explicit permitted output paths,
non-overridable invalidation, durable mutation evidence and historical receipt
compatibility. The final core gate ran **330 tests successfully, with 2 optional
SDK skips**. The final independent bounded R1 audit found no remaining actionable
issues; all **227 Bash assertions** and the generated-reference check passed.
See [R1 evidence](evidence/R1-candidate-integrity-2026-09-29.json) for the
full verification record, source fingerprints and limitations. The installed
runtime is not yet refreshed; that remains R8 work.

The review of `f4fa6577e2e891151231c9c7d3180be6e9e23faa` supersedes the earlier
claim that only credentials remain. M1/M2 remain accepted; M3's F1 source repair
is verified with installed refresh pending; M4 retains its real-Claude-host gate; M5–M7 have
independent repairs and integration work; C1 remains pending full-delivery
scope. R1 is the first implemented repair; do not confuse it with full closure.

Execute [SOL-REVIEW-FOLLOWUP.md](SOL-REVIEW-FOLLOWUP.md), starting at **R3**:
bind independent experiment evidence to saved runs, frozen assignments,
profiles and paired inputs; protect replay and future qualification decisions
against stale or unverified evidence. The plan contains bounded source-review
feedback and acceptance tests. Continue with routing/catalog/quota (R4), learning
runtime connections (R5), and normal terminal/readiness/check discovery (R6).
R7 covers C1 and R8 covers installed/live closure. Authentication and the Jev
key block their specific live subgates, not the independent engineering work.

The original September 29 review ran 317 Python tests successfully (2 optional-SDK
skips), 227 Bash assertions, generated-reference validation and an installed
payload comparison. An unclosed SQLite `ResourceWarning` still appeared and
is assigned to R5. Existing live receipts remain evidence of their exact runs,
not proof that the newly identified failure cases are safe. See the follow-up
plan for reproduction details and evidence limits, and
[backlog.json](backlog.json) for current work-package dependencies.

### Preserved implementation checkpoints

The following records describe earlier implementation and verification;
the review correction above governs current completion and next work.

- Workspace: `/Users/Dikshant/Desktop/Projects/devsquad`.
- Build branch: `codex/engineering-team`. `main` remains the published runtime
  baseline. Inspect current refs before acting; later build checkpoints may be
  local and must not be discarded.
- M1 is accepted for bounded truthful invocation/preparation at `97a10f0`.
  Its saved integrated Codex receipt SHA256 is
  `324c2ce6154936ecf71a8bf913a195befd4251efc6dbb8bbab3dc0dbe1b86df8`;
  the private receipt remains outside the repository under
  `/Users/Dikshant/.devsquad/private-probes/native-codex-20260907T025419Z-97a10f0ae1e8`.
- M2 is accepted at `ddb6f51`. Its durable store, gated runner, cancellation,
  recovery, schema-5 host handoffs and CLI/service operations pass 121 core
  tests with `ResourceWarning` promoted to failure and 202 Bash assertions.
  See [M2-STATUS.md](M2-STATUS.md) and the
  [portable redacted evidence](evidence/M2-durable-runs-2026-09-16.json).
- Final independent M2 review found two P1 races and no other defect in its
  bounded target. `ddb6f51` fixes both: lease authorization now samples time
  after acquiring the SQLite write transaction, and public cancel resumes an
  interrupted `recovery_cleanup`. Both have deterministic regressions.
- M3 was accepted at `1737667` and is now reopened for F1. The branch-review path includes frozen
  routing/input, native and offline reviewers, trusted checks, fenced host and
  headless lead disposition, complete waiting/terminal reports, cumulative
  budgets, transactional pool capacity and bounded frozen fallbacks. The final
  gate is 188 core tests with `ResourceWarning` promoted to failure plus 202
  Bash assertions. See [M3-STATUS.md](M3-STATUS.md) and the
  [portable closeout evidence](evidence/M3-branch-review-2026-09-22.json).
- The bounded real public `branch-review` gate passed at `9478796` with
  verified gpt-5.5/low, read-only ephemeral execution, one supported finding,
  a passing required check, native-reported usage and all five terminal report
  hashes. See the [portable redacted evidence](evidence/M3-native-codex-review-2026-09-17.json).
  That live receipt remains the M3 provider gate; closeout used offline tests
  and did not consume another provider turn.
- The independent M3 audit at `84deb77` found four runtime/reporting defects.
  `1737667` fixes all four with direct regressions: pre-launch recovery no
  longer consumes a fallback/budget slot, headless lead exhaustion terminalizes,
  unknown capacity allows one unresolved trial, and later failure/cancellation
  retains earlier attempts and dispositions. A requested follow-up agent rerun
  hit the shared Plus limit; the 188-test complete gate is green after the fixes.
- M4 Plan 06-01 is complete at `643910d`. The optional official MCP Python
  SDK is pinned and transitively locked at `mcp==2.2.0`; ordinary CLI and a
  plain installed wheel remain dependency-free. `squad mcp serve` exposes the
  eight saved-run operations with strict envelopes, bounded event pages and a
  16 KiB total artifact-preview cap. Worker-origin mutations are denied while
  read-only inspection remains available, and all four legacy hooks honor the
  worker/delegation guard. The gate is 200 core tests with `ResourceWarning`
  promoted to failure, 208 Bash assertions, and 12 focused tests against the
  installed official SDK.
- M4 Plan 06-03 has completed all independent work at `aa0fef5`. The stable
  isolated runtime at `~/.devsquad/releases/0.1.0+aa0fef5` is registered in
  all four real local host configs; `squad doctor` reports four installed and
  four matching registrations, and a second setup pass made no changes. One
  terminal-started saved run was inspected through the real Codex MCP host,
  then claimed/completed through official stdio SDK clients with identical
  ledger identity, a rejected competing claim and terminal receipt hashes.
  Closing the MCP client while a detached worker ran did not terminate it.
  The gate is 212 core tests, 208 Bash assertions and 22 pinned-SDK tests.
  M4 remains **blocked**, not complete, because its exact gate still requires
  a normally authenticated Claude Code host to perform the real handoff; the
  labeled SDK client is deliberately not presented as that proof. See the
  [portable redacted evidence](evidence/M4-local-mcp-2026-09-23.json).
- Last-observed provider readiness outside accepted M3: Claude CLI is not
  logged in and Grok CLI authentication is expired. Antigravity 1.2.13 is
  authenticated and its project-scoped `mcp(devsquad/squad_status)` grant has
  now produced a live Gemini receipt. Do not retry the blocked Claude/Grok
  paths, buy credits, use paid API fallback or change global provider settings.
  Resume those gates only after the user completes the corresponding login.
- User wants the implementation orchestrated efficiently and preserved across
  Plus-plan interruptions. Avoid recursive subagent fan-out: it consumed the
  shared window rapidly without advancing M3. The recursively created M3
  planning agents all hit the same Plus limit; continue locally until shared
  agent capacity is restored, then use only bounded leaf reviews.
- Full assignment remains **M1–M7 plus C1**, as specified in
  [SOL-HANDOFF.md](SOL-HANDOFF.md). The current review work packages R1–R8
  replace the earlier credentials-only assessment of M5–M7.
- M5 Plan 07-01 has started at `d96e9e4`. The core and Bash compatibility
  boundaries now include a Claude 2.1.220 headless adapter with structured
  output, version-scoped model/effort preparation, explicit Read/Glob/Grep or
  Edit/Write tool sets, strict empty MCP configuration, safe mode and no
  blanket permission bypass. Its offline gate is 215 core tests, a fresh-wheel
  content check and 220 Bash assertions. This is adapter conformance, not a
  live Claude model receipt; normal Claude login remains required.
- M5 Plan 07-01 delivery workspace/candidate freezing is complete at `6a7e849`.
  A detached run-owned implementation worktree now enforces declared write
  scope and symlink containment, creates one coordinator-owned local commit,
  returns stable commit/tree/patch/candidate hashes, replays idempotently and
  rejects later mutation. Five focused tests prove the original checkout,
  index, HEAD and remote refs remain unchanged. The complete gate is 220 core
  tests and 220 Bash assertions. One first core run observed the pre-existing
  coordinator-crash test return `live`; its isolated run and the complete rerun
  passed unchanged.
- M5 Plan 07-01 is complete at `0e88d73`. The offline implementer now executes
  inside the durable process/writer fence, validates frozen attempt evidence,
  serializes candidate finalization across recovery importers and atomically
  saves implementation, local commit and patch evidence before creating
  separate review/check worktrees. Concurrent resume creates no second writer;
  out-of-scope edits fail without a candidate. The complete gate is 224 core
  tests and 220 Bash assertions. A reproduced macOS zombie-only process-group
  ambiguity was fixed with a non-zombie inventory check and direct regression.
- M5 Plan 07-02 first-candidate review/check/handoff is verified offline at
  `30b98df`. The durable implementer creates a frozen candidate, explicit
  resume launches read-only review, separate trusted checks validate that
  candidate, and the host receives an `issue-delivery` handoff bound to its
  hash. The complete gate is 226 core tests discovered (suite OK, 2 optional
  SDK skips) and 220 Bash assertions. This does not complete lead disposition,
  revise-to-implementation, all terminal reports or the live two-harness gate.
- M5 Plan 07-02 bounded revisions are verified offline at `4c76887`, following
  delivery accept/reject receipts at `a1199c6`. A saved `revise` transaction
  now returns the writer fence to the delivery worktree, binds prior evidence
  into the next prompt, creates a distinct child candidate and new review/check
  worktrees, rejects stale live evidence and preserves both candidates plus all
  successful worker attempts and dispositions. Required checks remain
  non-overridable; revision/invocation exhaustion stops before a new writer; a
  simulated prelaunch crash resumes exactly one repair writer; and headless
  delivery acceptance uses its own fenced lead attempt. The gate is 237 core
  tests (2 optional-SDK skips) and 220 Bash assertions. Delivery fallback,
  failure/cancellation history, live Claude execution and the live two-harness
  proof remain open.
- M5 was assessed as offline-complete at `f199cd2`; F1/F2/G4 now reopen that assessment.
  `b7d90cc` freezes the real Claude implementation bridge, records observed
  identity/session/usage, enforces the same-permission rate-limit fallback and
  preserves delivery failure/cancellation history. `f199cd2` kills a live
  revised-implementation supervisor and proves retained ownership, no duplicate
  writer, successful reap and unchanged source checkout/remotes. The exact core
  gate is 241 tests with 2 optional-SDK skips and ResourceWarning promoted to
  error; the compatibility gate is 220 Bash assertions. Normal Claude login
  remains required for the genuine Claude implementation → different-model
  Codex review/check/disposition receipt, after the independent repairs.
- M6 shared-capacity work is verified through `d170c01`. Schema 9 persists
  strict scoped observations and reservations, backfills active schema-8
  attempts and keeps ambiguous ownership in flight. Two separate projects
  racing for an unresolved pool create one reservation; fresh exhausted weekly
  evidence beats short-window availability; stale/estimated/incomplete data
  remains unknown; reservation rechecks post-preflight changes; and routing
  applies model/profile sublimits without widening eligibility. `squad capacity
  observe --file FILE` and frozen/current status evidence are wired. The gate
  is 255 core tests with 2 optional-SDK skips and 220 Bash assertions. See
  [M6-STATUS.md](M6-STATUS.md).
- M6 outcome/report/experiment evaluation is verified through `edfb3f3`.
  Schema 10 records append-only final and late outcomes with truthful attempt
  contribution, and reports separate automatic, pinned and experimental
  evidence with sample size and missingness. Schema 11 freezes one-variable
  paired experiments, evaluates evaluation and held-out splits, retains every
  failure and rollback target, rejects conflicting replay and never changes
  active policy. The public path is `squad policy evaluate --experiment FILE`.
  `98c6685` adds `squad learn propose --project PATH`, which writes
  content-addressed local JSON/Markdown drafts from one consistent ledger
  snapshot, verifies saved evidence hashes and returns explicit no-change when
  evidence is absent.
- M6 profile lifecycle is verified through `ca4ee73`. Schema 12 stores
  versioned templates, immutable concrete profiles, bounded qualification,
  compare-and-swap bindings and immutable decision receipts. Promotions affect
  new runs only; exact pins and frozen runs do not float. `4b27e0c` binds
  ordinary regression rollback to a saved post-change held-out evaluation.
  `398ae6a` scopes complete catalog drift to affected profiles while keeping
  additions unqualified, and `ca4ee73` rolls a removed incumbent only to the
  newest prior proven/qualified profile under the same template or blocks
  without mutation. The exact gate is 275 core tests with 2 optional-SDK skips
  and 220 Bash assertions. These component checkpoints do not close the
  evidence-integrity and missing runtime connections now assigned to R3–R5.
- M6-D1 is verified at `87fa9cf`. The optional decision helper defaults to off;
  shadow records without changing execution, and advisory can only reorder the
  deterministic router's already-eligible profiles under reviewed gate
  evidence. Schema 13 fences and caches calls, prevents duplicate paid calls on
  replay, records launched-unknown outcomes as indeterminate, exposes run
  accounting in status and stores only hashes/byte counts for task evidence.
  Malformed, unknown-ID, NaN, pin, permission/quality, drift, cancellation and
  crash/resume cases fail closed. The frozen synthetic baseline explicitly
  keeps runtime adoption off. The gate is 291 core tests with 2 optional-SDK
  skips and 220 Bash assertions. M6-D2 remains blocked only on
  `TYPESAFE_API_KEY`; Laya remains conditional on its declared trigger.
- M7 packaging, normal task entry and the currently available live surfaces are
  verified through `b1d52ad`. The immutable standalone installer works without
  Claude, performs offline exact-lock MCP installation with `pip check`, emits
  clean JSON, migrates only a recognized legacy launcher, retains old releases
  and is idempotent. The active installed release is
  `0.1.0-py31214-9e5cdea2aa99-mcp-a26bc88afbef`; setup reports all four host
  registrations unchanged and doctor is ready. Bundled Codex
  0.155.0-alpha.9.2 passed native initialize plus a complete seven-model
  catalog and is preferred over PATH Codex 0.135.0. A terminal-created run was
  observed through real Gemini/Antigravity and Codex MCP calls, then cancelled
  from terminal. `85aa378` adds installed `squad review` and `squad fix`
  commands that resolve exact commits, discover the Codex catalog without a
  generation, embed hash-frozen routing and need no hand-written JSON. The
  offline fix gate creates one isolated writer, freezes the candidate, runs
  independent review and checks, preserves the source checkout and reaches
  host handoff; `--wait` advances the saved candidate-review phase once.
  `1553768`, `09e435c` and `9d70888` align structured-output recovery with
  Codex 0.155's completed agent messages and retain only redacted structural
  diagnostics. `b1d52ad` gives every trusted check a fresh temporary HOME so
  detached checks work without exposing the user's real home or sharing state.
  The exact `9d70888..b1d52ad` candidate then received a verified clean
  gpt-5.5/low read-only review; both frozen checks passed and the artifact-bound
  host acceptance terminalized succeeded. The gate is 317 core tests with 2
  optional-SDK skips, 227 Bash assertions, 8 focused installer tests and 22
  installed-SDK tests. See
  [M7-STATUS.md](M7-STATUS.md) and the
  [installed-runtime evidence](evidence/M7-installed-runtime-2026-09-29.json)
  plus [normal-entry evidence](evidence/M7-normal-entry-2026-09-29.json) and
  [live Codex review evidence](evidence/M7-live-codex-review-2026-09-29.json).
  R4/R6 now identify remaining independent work; normal Claude login, renewed
  Grok authentication and the installed two-model delivery are additional
  external gates.
- The user's Jev/Laya request is evaluated in
  [DECISION-CLASSIFIERS.md](DECISION-CLASSIFIERS.md). This source-backed plan
  amendment adds M6-D1–D3: default-off contracts/baseline, a one-request capped
  Jev pilot, and local Laya fallback plus measured adoption. It prioritizes routing
  hints, skill/tool shortlists and context ranking, followed by failure triage,
  review attention and outcome labels. No weights/inference/API spending or
  runtime routing changes occurred. The user has authorized one Jev request
  using only the synthetic fixture, no retries and at most $0.01. The tracked
  fixture/probe checkpoint is committed at `70e59cb` and five focused offline
  tests are ready; the complete offline
  gate is 231 core tests discovered (suite OK, 2 optional SDK skips) and 220
  Bash assertions. The live call is
  blocked because the TypeSafe console is at login and no `TYPESAFE_API_KEY`
  exists. Classifier suggestions never become permission/acceptance authority.
  Do not wait for this probe to execute the review repairs starting at R1.

## Completed and preserved

Branch cleanup is complete. Local/GitHub working branch names were consolidated into `main` and `codex/engineering-team`. The old assessment and holdout commits remain in their descendant histories. The unrelated February backup is preserved by its existing local tag and a verified complete Git bundle. See the [branch record](../../audits/2026-09-06-branch-consolidation.md).

The M1 implementation includes Python packaging/contracts, native Codex protocol preparation and framing, catalog-to-profile preparation, shared error-classification policy, strict input validation, and legacy timeout/context/catalog fixes. Earlier review defects have corresponding regression tests, including [the independent review cases](../../../test/core/test_m1_gate_review.py).

Verified at the implementation/evidence checkpoints above:

| Check | Result |
|---|---|
| Python core discovery | 317 tests passed through the M7 live Codex-review repair, with 2 optional-SDK skips and ResourceWarning promoted to error |
| Bash 3.2 regression suite | 11 test files, 227 assertions passed |
| Optional MCP boundary | `mcp==2.2.0` installed/constructed on local Python; Python 3.11 lock resolution; 22 official-SDK focused tests passed |
| M7 installed runtime | Current immutable release `0.1.0-py31214-9e5cdea2aa99-mcp-a26bc88afbef` has no source/plugin/installed drift; `pip check`, idempotent reinstall, four-host unchanged setup and doctor passed |
| M7 normal task entry | Exact-commit review and bounded fix need no hand-written JSON; an offline delivery passed writer/candidate/review/check/source-preservation/handoff gates, and a live installed Codex review passed both checks and host acceptance |
| M7 live surface proof | Terminal start/cancel plus real Gemini/Antigravity and ephemeral Codex `squad_status` calls observed the same run/version |
| M4 local host setup | Stable isolated runtime is registered in all four real local configs; doctor reports ready and a second setup pass was unchanged |
| M4 cross-surface proof | Real Codex read the terminal-started run through MCP; official SDK clients proved identical ledger, fenced claims, completion and disconnect survival; actual Claude handoff remains blocked on login |
| Wheel installation | Fresh external venv resolves packaged assets and applies migrations through schema 8 |
| Earlier live probes | Codex metadata and a separate read-only CLI smoke succeeded |
| Integrated native adapter proof | Passed at `97a10f0`; gpt-5.5/low, read-only, correlated completion and confirmed process-group cleanup |
| M2 crash/race matrix | Real subprocess interruptions plus independent-process start, writer, cancel, import and host-handoff races passed at `ddb6f51` |
| M3 deterministic routing | Strict profiles/policy, aliases, overrides, fallback and typed capacity tests passed at `ca55990` / `1c7b614` |
| M3 frozen review input | Exact OIDs/config hashes, detached worktree, moving-ref stability, dirty-input rejection and source checkout preservation passed at `cd9a881` |
| M3 offline workflow evidence | Strict candidate-bound review/check evaluation and durable separate-worktree execution passed at `a756307` / `97d2c6c` |
| M3 host disposition/reporting | Accept/reject/revise, required-check blocking, retry budgets, stale claims, crash resume and five terminal reports passed at `30cf49e` |
| M3 native Codex reviewer | Public start, exact identity verification, ephemeral read-only structured output, native usage and four provider-fault classes passed offline at `459ff3f` |
| M3 live public review | Passed at `9478796`; gpt-5.5/low found one supported regression, the required check passed, host acceptance terminalized succeeded and five report hashes were retained |
| M3 closeout | Waiting/failure reports, headless leadership, cumulative budgets, live pool fencing, runtime fallbacks and all four independent-audit fixes pass at `1737667` |

The first two saved-probe invocations failed before `Popen` because of
probe-only path/field defects, so neither launched Codex nor consumed a model
turn. Their private receipts remain under `~/.devsquad/private-probes`. The
probe now uses a dedicated process session, bounded group TERM/KILL cleanup,
an explicit terminal deadline, and retains early notifications for correlation.
The successful run retained separate stderr files of 138,030 and
285,644 bytes, supporting the diagnosis that an undrained stderr pipe caused
the earlier apparent nonresponses.

The authoritative requirement matrices are [M1-STATUS.md](M1-STATUS.md),
[M2-STATUS.md](M2-STATUS.md), [M3-STATUS.md](M3-STATUS.md),
[M5-STATUS.md](M5-STATUS.md), [M6-STATUS.md](M6-STATUS.md) and
[M7-STATUS.md](M7-STATUS.md). [backlog.json](backlog.json) marks M1/M2 complete,
M3/M5/M6/M7 in progress, M4 blocked on its real Claude handoff, and C1 pending.
The review correction and [Sol follow-up plan](SOL-REVIEW-FOLLOWUP.md) govern
where historical verification is incomplete. Unauthenticated or unsupported
provider paths must not be advertised as verified.

## Exact next work

1. Check Git status and recent commits, preserving work newer than this note.
   Continue `codex/engineering-team`; do not restart from `main` or redo M1/M2.
2. Implement the R3b saved-run v2 outcome reader and current-evidence
   eligibility. Join `experiment_specs` / `experiment_assignments` to actual
   role attempts and final/correction outcome rows; recompute original input
   and per-arm execution fingerprints from the immutable preparation witness.
   Validate every relevant attempt, including fallbacks/repairs. Finish
   R3b/R3c saved evidence, replay,
   qualification/promotion/rollback/catalog-fallback eligibility and historical
   compatibility, then execute R4–R6 in dependency order. Preserve R1/R2,
   explicit check `output_paths` contract and historical receipts. Do not
   rewrite the architecture or reset completed work.
3. Keep Claude and Grok live probes paused until normal login is restored.
   Afterward, run the M4 Claude handoff, the M5 installed Claude-to-Codex
   delivery and one bounded Grok operation, retaining only redacted evidence.
4. Keep M6 decision guidance off by default. Once `TYPESAFE_API_KEY` is
   supplied, run the prepared one-request synthetic Jev pilot immediately with
   no retry and the $0.01 ceiling. Install/run Laya only if the predeclared Jev
   cost/access/quality trigger fires.
5. Complete the separately gated C1 extension under R7 and audit installed/live
   closure under R8. C1 is required in the full assignment even though it does
   not reopen M7. Record each blocked subgate without pausing unrelated work.

The local official reference clone `/tmp/devsquad-codex-plugin-review-20260906` has native client patterns, including the `initialize` → `initialized` handshake. Installed protocol schemas were generated under `/tmp/devsquad-codex-protocol-20260906`. These temporary references may need to be regenerated after a restart; they are not the project source of truth.

## Checkpoint discipline

- Commit coherent partial work and its evidence at small intervals; do not wait for an entire milestone. Mark incomplete work accurately.
- Before long probes or a likely usage cutoff, update this recovery note and checkpoint. Keep the working tree clean at a pause; never stash.
- Run the required `bash test/run.sh` before each commit, and the relevant core tests for code changes. Record failing checks when saving a necessary WIP checkpoint rather than calling it complete.
- Keep raw private prompts, credentials and native diagnostic logs outside tracked evidence. Preserve reproducible scripts and redacted receipts in the repository.
- An account limit does not authorize purchasing credits, consuming a reset credit, silently using paid APIs or changing the requested implementation model. Resume when capacity is available or the user supplies new instructions.
- Do not promise execution while the account is blocked. The committed work and this file are the handoff across that interruption.

```bash
git status --short --branch
git log -6 --oneline
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s test/core -v
bash test/run.sh
```

Continue from the earliest unfinished requirement with available dependencies. Preserve all later implementation and review findings if this note is older than the current branch.
