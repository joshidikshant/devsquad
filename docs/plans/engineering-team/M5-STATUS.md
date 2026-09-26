# M5 implementation status

M5 is **in progress**. This matrix is derived from the authoritative M5
requirements before implementation; a row becomes verified only when its
behavioral evidence exists. The milestone remains incomplete until the live
two-harness gate passes.

| Requirement | Planned evidence | Status |
|---|---|---|
| Claude headless adapter | Manifest/argv conformance, exact model and effort validation, structured result faults, bounded permission/tool surface, recursion guard and installed-wheel contents | verified offline at `d96e9e4` |
| Isolated implementation | Run-owned detached delivery worktree at the frozen target, one active writer and original checkout/index/HEAD preservation | verified offline at `0e88d73` |
| Scoped local candidate | Out-of-scope and symlink-escape rejection; intentional untracked capture; local candidate commit and patch/hash artifacts; no merge, push or remote mutation | verified offline at `0e88d73` |
| Independent reviewer | Different verified model identity is mandatory and a different harness is preferred when qualified; unknown/same identity cannot count | router verified; workflow pending |
| Candidate-bound review/checks | Read-only review and separate check worktree bind to the exact candidate; changed candidate invalidates prior evidence | first-candidate review/check/handoff verified offline at `30b98df`; revision/stale-evidence workflow gate pending |
| Bounded correction/fallback | Seeded defect causes revise to implementation, then new review/checks; rate-limit fallback retains permissions and all finite budgets | pending |
| Non-overridable disposition | Missing implementation/invalid review/mandatory failing check block acceptance regardless of lead prose | pending |
| Complete result history | Receipt retains every implementer/reviewer/lead attempt, failed fallback, repair, revision, candidate and evidence hash | pending |
| Crash recovery | Killing a live implementation supervisor cannot create a duplicate writer on resume | pending |
| Live acceptance | One bounded issue completes across at least two authenticated subscription harnesses with different verified models | pending |

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
