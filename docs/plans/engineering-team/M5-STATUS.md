# M5 implementation status

M5 is **in progress**. This matrix is derived from the authoritative M5
requirements before implementation; a row becomes verified only when its
behavioral evidence exists. The milestone remains incomplete until the live
two-harness gate passes.

| Requirement | Planned evidence | Status |
|---|---|---|
| Claude headless adapter | Manifest/argv conformance, exact model and effort validation, structured result faults, bounded permission/tool surface, recursion guard and installed-wheel contents | verified offline at `d96e9e4` |
| Isolated implementation | Run-owned detached delivery worktree at the frozen target, one active writer and original checkout/index/HEAD preservation | pending |
| Scoped local candidate | Out-of-scope and symlink-escape rejection; intentional untracked capture; local candidate commit and patch/hash artifacts; no merge, push or remote mutation | pending |
| Independent reviewer | Different verified model identity is mandatory and a different harness is preferred when qualified; unknown/same identity cannot count | router verified; workflow pending |
| Candidate-bound review/checks | Read-only review and separate check worktree bind to the exact candidate; changed candidate invalidates prior evidence | pending |
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
