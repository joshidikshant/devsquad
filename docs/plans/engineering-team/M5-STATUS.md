# M5 implementation status

M5 is **in progress with independent repairs available**. The September 29
review at `f4fa657` reopened candidate integrity (F1), observed Claude identity
(F2) and complete normal-command check coverage (G4). F1/R1 is now repaired
and verified offline; R2 is also source/offline verified at `ef98889`. Execute R6 in
[SOL-REVIEW-FOLLOWUP.md](SOL-REVIEW-FOLLOWUP.md). The genuine Claude-to-Codex
live gate additionally remains blocked on normal Claude login.

R2 now enforces native reported model identity, strict imported evidence bound
to the actual attempt, independent-review acceptance and durable failure/
fallback/cancellation/legacy replay. Its 37 added regressions are included in
the final 367-test suite (OK, two optional-SDK skips); 227 Bash assertions and
the generated-reference check passed. The SQLite cleanup warning remains R5.
See [R2 evidence](evidence/R2-observed-identity-2026-10-01.json); the
[red baseline](evidence/R2-identity-red-baseline-2026-09-29.json) is retained as
history. This is not a corrected installed Claude live receipt or full M5 closure.

| Requirement | Planned evidence | Status |
|---|---|---|
| Claude headless adapter | Manifest/argv conformance, exact model and effort validation, structured result faults, bounded permission/tool surface, recursion guard and installed-wheel contents | argv conformance verified at `d96e9e4`; R2 observed-model evidence verified offline; unreported effort remains unknown |
| Isolated implementation | Run-owned detached delivery worktree at the frozen target, one active writer and original checkout/index/HEAD preservation | verified offline at `0e88d73` |
| Scoped local candidate | Out-of-scope and symlink-escape rejection; intentional untracked capture; local candidate commit and patch/hash artifacts; no merge, push or remote mutation | verified offline at `0e88d73` |
| Independent reviewer | Different verified model identity is mandatory and a different harness is preferred when qualified; unknown/same identity cannot count | R2 public host/headless identity gates verified offline; installed/live proof pending |
| Candidate-bound review/checks | Read-only review and separate check worktree bind to the exact candidate; changed candidate invalidates prior evidence | R1 check-mutation repair verified offline; R6 normal-command check coverage remains |
| Bounded correction/fallback | Seeded defect causes revise to implementation, then new review/checks; rate-limit fallback retains permissions and all finite budgets | correction/budgets verified at `4c76887`; same-permission delivery fallback verified at `b7d90cc` |
| Non-overridable disposition | Missing implementation/invalid review/mandatory failing check block acceptance regardless of lead prose | R1 candidate integrity and R2 actual-model identity enforced offline; installed/live proof pending |
| Complete result history | Receipt retains every implementer/reviewer/lead attempt, failed fallback, repair, revision, candidate and evidence hash | success, repair, fallback, failure and cancellation history verified offline at `b7d90cc` |
| Crash recovery | Killing a live implementation supervisor cannot create a duplicate writer on resume | prelaunch and live revised-writer recovery verified offline at `f199cd2` |
| Live acceptance | One bounded issue completes across at least two authenticated subscription harnesses with different verified models | pending repairs and normal Claude CLI login |

## Boundary

M5 implements only the fixed `issue-delivery` sequence. It does not add an
arbitrary DAG, broad autonomous project implementation, automatic integration,
merge, push or publication.

## Plan 07-01 checkpoint 1

The Claude CLI adapter is available through both the core manifest boundary and
the Bash 3.2 compatibility API. Its verified 2.1.220 profile uses structured
non-interactive output, safe mode, no session persistence, an empty strict MCP
configuration, no browser, explicit role tools and no blanket permission
bypass. The read-only profile exposes only Read/Glob/Grep; the write profile
adds Edit/Write but not Bash or Agent. Offline evidence is 3 focused tests, 215
full core tests (2 optional-SDK skips), a fresh wheel containing the manifest,
and 220 Bash assertions. This does not claim a live Claude model invocation;
the installed CLI still requires normal provider login.

## Plan 07-01 checkpoint 2

The delivery workspace starts from the frozen target in a detached, run-owned
Git worktree. Candidate freezing rejects out-of-scope edits and escaping
symlinks, stages only after validation, disables repository hooks/signing,
creates one coordinator-owned local commit, and returns stable commit/tree/
patch/candidate hashes plus added-file evidence. A replay returns the same
candidate; any later workspace edit invalidates it. Five focused tests prove
file names with spaces, no-change rejection, scope and symlink failures,
source checkout/index/HEAD preservation and unchanged local-remote refs.

The complete gate is 220 core tests (2 optional-SDK skips) and 220 Bash
assertions. The first full discovery observed the pre-existing coordinator-
crash race test return `live` once; that isolated test and the full discovery
rerun passed unchanged. Connecting this workspace to the durable implementer
attempt and its existing store-level writer fence remains the next slice.

## Plan 07-01 checkpoint 3

The offline implementation worker now runs inside the durable M2 process and
writer fence. Its frozen prompt/profile evidence must validate before the
coordinator serializes candidate finalization, rechecks scope, commits locally,
creates independent review/check worktrees and atomically imports the
implementation, candidate and patch artifacts into the same saved ledger.
A concurrent resume observes the live owner and creates no second attempt.
An out-of-scope implementation terminalizes failed without publishing a
candidate. The original checkout and remote refs remain unchanged.

The complete gate is 224 core tests (2 optional-SDK skips) and 220 Bash
assertions. During this slice a full run reproduced an existing macOS race in
which a zombie-only process group was classified `ambiguous`; `0e88d73` now
uses the non-zombie process-group inventory before declaring ambiguity and has
a direct regression. The isolated service/supervisor/delivery suites and the
complete discovery pass after that fix. Plan 07-02 now owns independent review,
checks, disposition and revision behavior.

## Plan 07-02 checkpoint 1

At `30b98df`, review prompts/evidence accept `issue-delivery` only when the
workflow matches the frozen task. A pending offline review fixture is bound
to the actual candidate after implementation; the durable reviewer then runs
read-only, imports its candidate-bound evidence, executes trusted checks in
the separate worktree, and publishes the correct delivery lead handoff.

The end-to-end offline regression proves implementation → explicit candidate
resume → review/checks → lead claim, with matching candidate hashes and no
source-checkout mutation. This is not native identity verification or a live
two-harness result. Lead completion, revise-to-implementer iterations, full
terminal history and the remaining M5 fault/live gates are still pending.

The complete core discovery is **226 tests, suite OK with 2 optional-SDK
skips**, with `ResourceWarning` promoted to error; **220 Bash assertions**
passed. The next implementation slice is Plan 07-02 Task 3, retaining Task 2's
unproven live/stale-revision requirements rather than marking M5 complete.

## Plan 07-02 checkpoint 2

At `a1199c6`, host accept/reject applies the trusted gate to `issue-delivery`,
blocks acceptance after a required-check failure, and emits the complete
five-report terminal set with implementation plus review evidence.

At `4c76887`, `revise` atomically consumes the saved handoff, switches the
writer fence back to the delivery worktree, binds prior review/check evidence
into a frozen revision request and launches a new implementer. The next local
commit is parented by the prior candidate while its identity and patch still
describe the complete baseline-to-candidate change. Each iteration gets
separate review/check worktrees, so stale live evidence cannot validate
against a replacement candidate; terminal history validates each archived
candidate against its own saved workspace.

The seeded repair passes implementation → failed mandatory check → host revise
→ new implementation → new review/check → accept with distinct candidate
hashes and roles `[implementer, reviewer, implementer, reviewer]`. Revision and
invocation exhaustion fail before another writer launches, a saved prelaunch
revision resumes to exactly one repair writer, and a headless delivery lead
terminalizes through its own fenced attempt. The complete gate is **237 core
tests (2 optional-SDK skips)** with `ResourceWarning` promoted to error and
**220 Bash assertions**. Delivery fallback/failure/cancellation history, live
Claude implementation and the live two-harness proof remain open.

## Plan 07-02 checkpoint 3

At `b7d90cc`, the frozen delivery package can invoke the exact preflighted
Claude binary without importing source-tree adapter resources at runtime. It
retains the requested and observed identity, native session and usage evidence,
classifies provider faults through the frozen contract, and permits only the
predeclared same-permission fallback. A rate-limited implementer therefore
uses the one frozen fallback without widening tools or permissions, and the
terminal receipt retains both attempts. Delivery-specific cancellation keeps
the prior candidate, attempts and lead disposition visible instead of
collapsing history.

At `f199cd2`, a real controlled subprocess kills the delivery supervisor while
a revised implementation child is live. Recovery reports retained ownership,
does not launch a duplicate writer, and cancellation reaps the process while
the source checkout and remote refs remain unchanged. The exact checkpoint
passes **241 core tests with 2 optional-SDK skips** and ResourceWarning promoted
to error. The compatibility gate remains **220/220 Bash assertions**; the last
code change after that run added only the delivery-specific Python regression.

This checkpoint's independent-completion assessment was superseded by the
September 29 review. Repair F1/F2/G4 before the genuine implementation and
different-model review/check/disposition gate. Normal Claude login remains
required for that redacted two-harness receipt.
