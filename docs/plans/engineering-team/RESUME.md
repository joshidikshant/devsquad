# Resume DevSquad after an interruption

This is the authoritative recovery entry point, not a chronological chat log.
Compare it with Git status/recent commits and retain newer work. Earlier notes
remain recoverable in Git; detailed receipts and failed gates stay in evidence.

## Current checkpoint — October 2, 2026

Continuation: R3c final independent closure audit is next, against immutable
`39b95f1` and the existing 477-test full gate. No required R3/R4 acceptance is
being silently waived. A coherent **partial R4** source slice is checkpointed:
native normal commands now call account/config/model/quota read-only RPCs;
scoped private last-good catalogs use a 24-hour TTL, OS refresh lease and
two-minute failure backoff. Native windows enter the shared typed capacity
ledger. Alias account/config/catalog mismatches block for requalification;
explicit pins remain trials. No login, reset-credit or API fallback calls.
New cache/concurrency/privacy/window and two-project discovery tests are added.
Focused/full/independent package closure and fresh-install proof are still
required before R4 is closed. One real non-generating native discovery probe
verified gpt-6.1-sol/low, scoped pool/catalog fingerprint and two quota windows;
reported capacity was unknown (not guessed available). Continue R3c audit,
then R4 public preflight/lease/identity acceptance, R5, R6 and R7/C1.

Workspace: /Users/Dikshant/Desktop/Projects/devsquad.
Branch: `codex/engineering-team`; never restart this build from main.
Source runtime repairs and the accepted G4 candidate below are **integrated
and installed at `6d2e0ba`**; all five blob hashes
exactly match the accepted candidate. Local affected gate: 27 tests in 18.940s,
plus 227 Bash assertions, generated-reference and diff checks passed.

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

The final **complete-diff branch review** is **succeeded/version 22**, accepted:
**`a6ecd887-5122-4281-b988-4d344a662e20`**, exact base
`1781b89b1228dfca2ca9148df437af3ac04b971f` → candidate `f8c4f8c…`.
Its scope is only the five changed files, with mandatory diff/Bash/affected
tests; no duplicate full gate. All checks passed with unchanged verified
integrity; 27 affected tests in 24.173s. Verified native Codex review is clean.

### Exact next action

1. The user's three requested runtime actions are verified. Do not repeat
   these accepted proofs or the unchanged full suite. Both exact packets and
   artifact hashes are verified; never edit frozen evidence.
2. Audit R3b.2/R3c closure against SOL-REVIEW-FOLLOWUP.md and the requirement
   matrix, preserving existing reader/eligibility/compatibility repairs. Then
   continue R4 catalog/quota, R5 public trials/outcomes, remaining R6 UX and
   R7/C1 in dependency order. Whole-plan acceptance is not claimed.
3. Desktop UI proofs remain separate. Antigravity IDE control is permission-
   denied; do not bypass it or substitute a CLI receipt. Grok/Gemini MCP status
   calls do not prove automatic writer/reviewer adapters.

Only one full core suite may run at a time; freeze source/tests while it runs.
Use the tracked spawn-safe scripts/run-core-tests.py, never a stdin main.
Run bash test/run.sh to completion before every commit. Keep a clean tree;
use git-safety checkpoints, never stash. No goal is currently active.

## Current local installation and host proof

Stable launcher: /Users/Dikshant/.local/bin/squad.
Selected release: `0.1.0-py31214-01fad439adea-mcp-a26bc88afbef`.
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
  Final-install recheck actually observed accepted run 360a4993, succeeded/31,
  in 12.118s. Existing project-only status grant, plan/sandbox, no bypass.
  IDE UI permission was denied; do not bypass it.
- Initial installed SDK gate: 22 passed/no skips; final installed transport
  gate: 9 passed in 2.803s, no skips. Reinstall unchanged, no payload drift,
  pip check/doctor passed, all four host registrations ready/unchanged.

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
