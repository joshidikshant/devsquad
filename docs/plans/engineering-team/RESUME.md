# Resume DevSquad after an interruption

Read this checkpoint, backlog.json and SOL-HANDOFF.md, then compare Git status
and recent commits. Retain newer work. Continue codex/engineering-team; no stash,
reset or restart from main. Detailed receipts and failed gates remain in evidence.

## Current checkpoint — October 2, 2026

The user-authorized public GitHub core release is complete:
https://github.com/joshidikshant/devsquad/releases/tag/v0.11.1.
This ships Claude plugin0.11.1 and standalone core0.1.0, not a registry or
hosted service. The broader engineering-team plan remains incomplete.

PR2 final headbefc7e8a00c9b9251af0a83e3a77f12adae5db11 passed all four
CI37090495321 jobs: Bash261/11 (nine packaging), Python3.11.9 full604/
900.372s, Python3.14.7 full604/869.101s, optionalMCP23/3.809s. Full core
jobs each had two optionalSDK skips/no failures/errors/unraisable; MCP one
optional skip. Mergecddfa6889eb62a0269fe11eb544e4b165e3c5c68 at
2026-10-03T02:55:39Z has identical tree52bca9df0a4c891f77642054c05d01829cfc5424.
Tag exactmerge; release402293874 published2026-10-03T02:58:18Z, latest,
not draft/prerelease. Archive headers and downloaded asset checksums verified.
Wheel reused only after exact core tree/file equality;11schemas/17migrations.

Original v0.11.0/PR1 also passed all-four CI and exact-merge asset checks,
but actual normal local plugin update caught missing outer hooks object.
v0.11.1 corrects only that wrapper/metadata/tests; commands, matchers and
15s timeouts unchanged. Independent frozen review clean, actual corrected
Claude2.1.220 validator passes; exact-old shape fails. Normal user-scoped
updater installed0.11.1/enabled/exit0; actual installed validator passes,
plugin list has zero load errors, hook blobfdbd2f3299951aa4e98c2bb354ae05667e8939f0
exactly equals reviewed source. Start a new Claude Code session to load it.
Original0.11.0 tag/assets stay unchanged; release notes direct users to0.11.1.

Earlier watchdog/dead-wait/blocked-phase failures and controlled red/green
test-only corrections remain in release evidence. Failed/cancelled jobs are
not passing gates. Duplicate push/main CI was cancelled after exact tree
equality to the complete accepted PR workflow. No new provider generation.

Final recovery Bash261/11, reference/JSON/diff checks pass. Only documentation
differs from released main. Finished r6-readiness/r6-terminal/r7-council
worktrees are archived with recoverable Git snapshots; only disposable ignored
bytecode was omitted. All production run/evidence worktrees remain intact.

## Accepted gates — do not repeat unchanged

- Frozen core b83dda7fbf691503d3adf3c9ea6ecebd4071ba16 passed604 tests/
  793.462s, two optional SDK skips, zero failures/errors/unraisable diagnostics.
  Core tree62eea7fa31153ef732fd5e9dfd97951d95b367d8 is unchanged by the
  subsequent legacy Bash/CI repair and documentation commits.
- Actual pristine accepted-R5 trusted-check preflight passed60 tests/33.182s
  with unchanged tracked/all-input fingerprints and zero undeclared bytecode.
  Input fingerprints are not branch-review candidate identities.
- Exact native EOF/fixture audit cb68f3ae-bea0-4744-90c4-96f25418fb8c:
  verified Codex0.159.2/gpt-6.1-sol/low/read_only, clean, succeeded22/host accept.
  Four required checks and integrity passed;60 affected tests/36.416s.
  Native95970 tokens are not an invoice or Plus-window forecast.
- R3/R4/R5 accepted closure evidence and earlier live Claude CLI handoff,
  Claude implementation → different verified Codex review → tests, and
  Grok MCP operation remain valid for their recorded candidates/scopes.
- Final legacy watchdog checkpoint3fdc6bd065c577a9ed59566573fdce399e9b585a
  is integrated. Real-deadline FIFO timer, buffered cancellation and restored
  caller EXIT trap preserve Bash3.2/optional-jq and original timing limits.
  Root integrated259 Bash assertions/45 affected Python tests0.927s pass;
  agent gates also pass; independent final
  review clean with50 legacy assertions. Exact reviewed adapter/test blobs:
  1847357c19677f276b06886386e2ac93e586b203 /
  8cc3fe9f9ee9df77b2fcb2d75c76c236a1f8d13a.
  Initial CI deadline drift, rejected timer designs, caller-path cleanup defect
  and corrected scheduling-only fixture failure remain honestly recorded.

## Installed runtime and surfaces

Current immutable release:
0.1.0-py31214-303a0e0a6c87-mcp-a26bc88afbef; Python3.12.14/MCP2.2.0/schema17.
Stable launcher /Users/Dikshant/.local/bin/squad. Zero nonterminal runs before/
after update; pre16 online backup0600/integrityOK and old releases retained.
Reinstallchangedfalse/all driftfalse/manifests match/pipcheck pass, zero downloads.
Nine actual installed SDK tests/3.126s pass with zero skips/failures/errors.
Doctor ready for branch review and Claude→Codex delivery; four registrations
match, subscription authentication verified for Claude/Codex.

Updated Antigravity means **agy CLI1.2.14**, NOT IDE. Actual
Gemini3.8FlashLow devsquad/squad_status observed cb68 succeeded22 in18.654s.
It read only its own MCP schema/generated result, not project files/other
servers; no zero-file-read or automatic-worker claim.
Grok/agy automatic worker readiness remains unknown; MCP proof is separate.

This chat's existing Codex MCP server returned SCHEMA_UNSUPPORTED because it
still uses an old package. New CLI and agy succeed on the same saved run.
A scoped DevSquad-only reconnect request is pending; do not change global
settings or repeatedly probe the unchanged old connection. This does not block
the public CLI release. Local Claude plugin0.11.1 passes actual installed
validation with no load errors; only DevSquad's normal user-scoped update ran.

## Exact next action

Public release/update gates are finished. Do not rerun accepted core/full/
native/installed/provider gates unchanged. Remaining R8 action is the user's
DevSquad-only MCP reconnect in this Codex chat, followed by one bounded saved-
run read once they confirm reconnection; do not repeatedly probe the old server.
Claude Code-tab/TCC proof and native Council remain separate external gates.
No need to reload this old chat or repeat architecture work. Read these files.

## Residual scope and boundaries

Council mechanics/schema17/reconciliation are integrated and offline tested.
Final nongenerating diagnostic is exhausted: sandbox network_request_failed/
noHTTP, unsandboxed control strict-response rejection; zero generating calls.
Native Council remains unavailable; fixture comparison inconclusive, automatic
OFF. No additional network attempts or permission widening.
Jev OFF; the single authorized pilot is spent. Laya is conditional, not adopted.
Claude desktop Code-tab proof remains unverified/TCC; CLI is not a substitute.
Rejected diagnostic import/cancel overlap remains a possible liveness follow-
up, not a confirmed unsafe finding: no error-time ledger snapshot establishes
ordering/final rows/lost intent. Completion fence failed closed; corrected
diagnostic cleanup waits for import DONE. No further probe is authorized here.
No credit purchase, reset redemption, paid API fallback or global AI settings
change. Raw provider diagnostics/credentials remain outside tracked evidence.

Evidence: evidence/public-release-0.11.1.json (published patch/install proof),
evidence/public-release-0.11.0.json (core/live/install/failed-gate history),
evidence/legacy-watchdog-release-repair.json, R6-terminal-readiness-partial,
evidence/release-receipt-synchronization.json and
evidence/release-runner-exit-synchronization.json,
R7 reconciliation/final-network diagnostics, R8-installed-workflows and
R3/R4/R5 closure files. Historical recovery detail is preserved in Git.
