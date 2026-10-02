# Resume DevSquad after an interruption

This is the authoritative recovery entry point, not a chronological chat log.
Compare it with Git status/recent commits and retain newer work. Earlier notes
remain recoverable in Git; detailed receipts and failed gates stay in evidence.

## Current checkpoint — October 1, 2026

Workspace: /Users/Dikshant/Desktop/Projects/devsquad.
Branch: `codex/engineering-team`; never restart this build from main.
Source runtime repairs through `1781b89` are installed. The accepted G4
candidate below is **not yet integrated or installed**.

### Latest verified result

Genuine saved issue delivery **`360a4993-d016-406e-8bb4-ae6d57dfe6cf`** is
**succeeded/version 31**, with fenced host acceptance:

- Exact candidate: `f8c4f8c83f170870eb37d564e54eee6188fc233c`.
- Candidate SHA-256: `3f0fa0e86edd8a4c7b04bc1a2447d70b136e596cafe3624826f1f46c2fcee568`.
- Verified native Claude Sonnet 5 writer (Claude 2.1.220); independent verified
  Codex gpt-6.1-sol/low read-only reviewer, clean, no findings.
- All three mandatory checks passed with unchanged verified integrity: diff,
  227 Bash assertions, **477 core tests in 455.860s**, two optional-SDK skips,
  zero errors/failures/unraisable diagnostics. UTC/monotonic ~456.22s agree.
- Explicit offline DEVSQUAD_BUILD_PYTHON made installed-wheel gates execute.
- Real Git README/symlink/Unicode/byte-documentation/byte-test cases all pass.
  Byte-name fixtures use index plumbing because APFS rejects those names.

The final **complete-diff branch review** is running:
**`a6ecd887-5122-4281-b988-4d344a662e20`**, exact base
`1781b89b1228dfca2ca9148df437af3ac04b971f` → candidate `f8c4f8c…`.
Its scope is only the five changed files, with mandatory diff/Bash/affected
tests; no duplicate full gate. Private observe-run.py stops at its handoff.

### Exact next action

1. Inspect that combined review's packet and verified artifact hashes. Accept
   only a clean review with all mandatory checks passed and unchanged integrity.
2. Integrate the accepted full five-file diff through apply_patch and verify
   each source/test blob against `f8c4f8c…`; run affected/Bash/reference gates.
   A prepared patch is cached in functions store, but regenerate from Git if
   unavailable. Never edit a frozen run/candidate/receipt.
3. Checkpoint, safely refresh the immutable local installation offline using
   existing Python 3.12.14 and MCP 2.2.0 wheelhouse; verify idempotence, drift,
   pip check, doctor, matching host registrations and installed SDK tests.
4. Run **one** bounded Gemini/Antigravity CLI/MCP status recheck against the
   final installed launcher and accepted run. Do not repeat completed Grok or
   Claude host proofs.
5. Record final results/limits in backlog and installed evidence. Whole-plan
   R3/R4–R7/C1 closure remains separate; use SOL-REVIEW-FOLLOWUP.md next.

Only one full core suite may run at a time; freeze source/tests while it runs.
Use the tracked spawn-safe scripts/run-core-tests.py, never a stdin main.
Run bash test/run.sh to completion before every commit. Keep a clean tree;
use git-safety checkpoints, never stash. No goal is currently active.

## Current local installation and host proof

Stable launcher: /Users/Dikshant/.local/bin/squad.
Selected release: `0.1.0-py31214-68d542f6e8ea-mcp-a26bc88afbef`.
Python 3.12.14/MCP 2.2.0, schema 15; previous releases and private pre-upgrade
SQLite backup retained. Source/plugin/installed payload drift is false.
Upgrade defers for old active/recoverable runs, swaps without migration under
the lock, then lazily migrates with old-client write guards.

- Real Claude MCP lead claimed/renewed/completed
  `b9501b55-1c65-4b32-a03f-1087cca8fefc`, succeeded/version 23.
  This is CLI/portable handoff, not desktop local Code-tab UI proof.
- Grok user-approved normal updater installed 1.0.46 stable; old executable
  backed up privately, login/settings preserved. Native grok-4.7-build actually
  called DevSquad status. This is not automatic Grok writer/reviewer proof.
- Antigravity 1.2.13/Gemini 3.8 Flash Low actually called the same MCP status.
  Existing project-only grant, plan/sandbox; final-install recheck remains.
  IDE UI permission was denied; do not bypass it.
- Initial installed SDK gate: 22 passed/no skips; transport follow-up: 9 passed.
  Final refreshed-install SDK gate still pending.

## Repairs and failure history to preserve

Full portable redacted record:
[evidence/R8-installed-workflows-2026-10-01.json](evidence/R8-installed-workflows-2026-10-01.json).

- Claude argv variadic tools consumed the prompt: explicit -- separator fixed.
- Strict bounded native JSON/stream identity verifies one session-correlated
  writer model, retaining auxiliary usage. firstParty maps to Anthropic only
  for verified exact Claude 2.1.220; unknown serving revisions stay unknown.
- Detached login needs HOME plus USER. Minimal environment and HOME-only
  reproduced synthetic not-logged-in output. Allowlist repaired; no API keys
  or provider overrides inherited; native auth errors fail without identity.
- `45667697…` writer succeeded, but reviewer runner/child died without a
  receipt; cause unknown. Dead ownership safely cancelled/version 17.
- `288ee6f9…` clean native review, but 471-test gate had three missing
  build-interpreter helper errors. Host rejected, failed/version 31.
- `33c64397…` first iteration passed real 475-test workflow, but exact
  combined review `ba14b743…` found strict UTF-8 byte-name decoding; rejected.
  Its fenced Claude revision ignored the new issue and made no edits. Prompt
  hash/reason were validated; correctly failed/version 37 for no changes.
  It is **not** a quota timeout or accepted candidate. Fresh task above fixed it.
- G4 now uses exact target Git regular blobs/trees, actual tracked test*.py,
  Bash plus core checks, required checks, exact-argv dedup and byte/NUL-safe
  filename parsing. Three wheel-test helpers catch unavailable interpreter
  candidates without relaxing isolation/dependency checks.

## Broader plan / constraints

M1/M2 remain accepted. R1/R2 source/offline repairs are verified; R3 saved-run
reader, eligibility/revisions, provenance/ratio and upgrade repairs have full
and bounded evidence, but final package-level closure audit remains open.
Normal aliases/public promotion proof exist; this does not close R4 catalog/
quota, R5 public trial controller/outcomes, remaining R6 UX or R7/C1 Council.
Read backlog.json and SOL-REVIEW-FOLLOWUP.md for dependency/acceptance details;
do not restart the architecture exercise or weaken gates to mark these done.

Jev .env is private/ignored and complete. Exactly one authorized pilot request
was spent; 8/8 family and skill labels, 6/8 tier labels including a confident
wrong tier. Runtime classification remains OFF; no adoption benefit or Laya
setup is proven. Do not repeat the pilot or silently use hosted/API fallback.

No credit purchases/resets, paid API fallback, global AI settings, push/merge,
deploy or external messages are authorized. Raw provider output and credentials
stay outside Git. Native reported cost/usage is not an inspected subscription
invoice or a reliable number of Plus five-hour windows.

Privacy: an earlier global Antigravity MCP listing exposed an unrelated
StitchMCP credential; user was advised to rotate it. Never repeat/store it.
Filter future diagnostics to DevSquad only; do not change unrelated credentials.

Private bounded helpers are under
/Users/Dikshant/.devsquad/private-probes/r8-installed-workflows-20261001:
observe-run.py, inspect-handoff.py, complete-handoff.py, start-combined-review.py,
g4-candidate-cases.py and probe.py. Claims/raw logs remain private; never reuse
the completed b950 handoff's prior claim for a different run.
  Jev pilot, and local Laya fallback plus measured adoption. It prioritizes routing
  hints, skill/tool shortlists and context ranking, followed by failure triage,
  review attention and outcome labels. No weights/inference/API spending or
  runtime routing changes occurred during that original planning pass. The user authorized one Jev request
  using only the synthetic fixture, no retries and at most $0.01. The tracked
  fixture/probe checkpoint is committed at `70e59cb` and five focused offline
  tests are ready; the complete offline
  gate is 231 core tests discovered (suite OK, 2 optional SDK skips) and 220
  Bash assertions. The live call was blocked until the October 1 key setup;
  it has now run once as recorded above. Classifier suggestions never become permission/acceptance authority.
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
2. R3b.1's source/offline gate passed at `a4a87fd`; do not repeat unchanged
   tests. Implement R3b.2 shared current-evidence eligibility and append-only
   evaluation/review revisions. Validate every relevant attempt, including
   fallbacks/repairs. Finish
   R3b/R3c saved evidence, replay,
   qualification/promotion/rollback/catalog-fallback eligibility and historical
   compatibility, then execute R4–R6 in dependency order. Preserve R1/R2,
   explicit check `output_paths` contract and historical receipts. Do not
   rewrite the architecture or reset completed work.
3. Claude login is now confirmed; the user reports Grok signed in. After the
   repaired installation is safely refreshed under R8, run the M4 real Claude
   handoff, the M5 installed Claude-to-Codex delivery and one bounded Grok
   operation, retaining only redacted evidence. Authentication is not itself
   a supported-operation receipt.
4. Keep M6 decision guidance off. The one-request Jev pilot is complete and
   must not be repeated under its spent authorization. Preserve its tier
   disagreements and frozen labels; a broader shadow/adoption comparison needs
   predeclared gates and a separate budget. Install/run Laya only if the
   declared cost/access/quality trigger is established.
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
