# M7 implementation status

M7 has **completed all independently executable packaging and normal-entry
work**. The standalone runtime is installed and usable from terminal, Codex
and Antigravity. M7 remains blocked on normal Claude and Grok authentication;
the installed different-model delivery is the same external gate retained by
M5.

| Requirement | Evidence | Status |
|---|---|---|
| Fresh standalone install without Claude | Immutable base install and composite `--core-only` regressions | verified |
| Optional MCP environment | Exact offline lock, one-line JSON, `pip check`, official SDK tests | verified |
| Reinstall/update safety | Idempotence, drift detection, stable launcher, retained old release and active-run survival | verified |
| Legacy plugin migration | Complete `plugin/core` package, no duplicate global hooks, second setup unchanged | verified |
| Host registration | Four installed hosts load the stable launcher and report matching | verified |
| Terminal operation | Fresh saved run started and later cancelled at version 20 | verified live |
| Codex App/CLI operation | Ephemeral gpt-6-luna/low called installed `squad_status` on the saved run | verified live |
| Antigravity operation | Gemini 3.8 Flash Low called installed `squad_status` with one project-scoped grant | verified live |
| Claude Code operation | Registration matching; real operation requires normal login | blocked externally |
| Grok Build operation | Registration matching; real operation requires renewed authentication | blocked externally |
| Installed two-model delivery | Genuine Claude implementation followed by different-model Codex review/check/disposition | blocked with M5 |
| Documentation/CI | Runtime guide, generated command reference and macOS/Python/optional-MCP workflow | clean-home install/setup/doctor passed |
| Normal task entry | Installed `squad review --base ...` and `squad fix "..."` dry-runs plus an offline end-to-end delivery | verified |
| Live normal Codex review | Exact commit range, verified read-only gpt-5.5/low review, isolated trusted checks and artifact-bound host acceptance | verified live at `b1d52ad` |

## Current installation

The stable launcher is `/Users/Dikshant/.local/bin/squad`. The active immutable
release is
`0.1.0-py31214-9e5cdea2aa99-mcp-a26bc88afbef`, using Python 3.12.14 and
`mcp==2.2.0`. A repeated install reports `changed=false`, `pip check` passes,
and `squad setup` reports every host unchanged.

Codex capability drift was revalidated rather than inferred. Bundled
`codex-cli 0.155.0-alpha.9.2` passed `initialize` and a complete seven-model
`model/list`; the runtime now chooses that verified binary over PATH Codex
0.135.0. Unknown versions remain fail-closed.

## Test gate

- 317 Python core tests passed with `ResourceWarning` promoted to error; two
  optional-SDK tests skipped in the dependency-free interpreter.
- 227/227 Bash assertions passed across 11 files.
- Eight focused installer tests passed.
- Twenty-two focused tests passed in the installed official MCP environment.
- The installed runtime passed `pip check`; setup and doctor report ready.
- The installed `review` and `fix` dry-runs resolved exact Git commits and the
  requested verified Codex profile without a model generation. The offline fix
  simulation created one isolated writer, froze its candidate, ran independent
  review and checks, preserved the source checkout and reached host handoff.
- The installed live `review` path selected verified Codex 0.155/gpt-5.5/low,
  returned a clean exact-candidate review, passed both frozen checks and
  terminalized `succeeded` after an artifact-bound host acceptance.

The recurring interpreter-finalization `ResourceWarning` was printed as an
unraisable cleanup diagnostic during full discovery, but the warning-as-error
process completed successfully. It is retained as an observation, not
misreported as a failing test.

## Live receipts

The terminal-created run `f121e96b-502a-4986-a3c3-7ba8ab418bb3` was observed
through both Antigravity and Codex as `awaiting_host`, version 14, with next
action `claim_handoff`. Terminal cancellation then moved the same run and its
open handoff to `cancelled`, version 20. Raw provider output remains private;
hashes and redacted usage are in
[the portable M7 evidence](evidence/M7-installed-runtime-2026-09-29.json).
Normal-entry installation and test details are in
[the normal-entry evidence](evidence/M7-normal-entry-2026-09-29.json). The
subsequent installed live review is recorded in
[the live Codex evidence](evidence/M7-live-codex-review-2026-09-29.json).

The live review run `c611ad4c-5473-4f05-a870-a136f24464d3` bound
`9d70888..b1d52ad`, observed verified gpt-5.5/low read-only execution, returned
zero findings and passed both `git diff --check` and `bash test/run.sh` inside
the trusted check workspace. Its host disposition accepted the exact four
artifact hashes and terminalized at version 22. This proves the installed
Codex review path; it does not replace M5's still-required genuine Claude
implementation half of the two-harness delivery gate.

Antigravity headless mode requires an explicit project grant for unattended
inspection. The verified minimum is `mcp(devsquad/squad_status)`; no global or
blanket permission bypass was used. Codex used an ephemeral minimal config,
read-only sandbox, no conversation resume and only the DevSquad MCP server.

## Exact remaining work

1. After normal Claude login, execute the saved M4 handoff and the M5 genuine
   Claude implementation to different-model Codex delivery.
2. After Grok login renewal, record one supported Grok Build operation against
   the same installed runtime.
3. Re-run the final gates and mark M7 complete only when every required surface
   receipt and installed delivery is present.

`squad council` remains attached to the separately gated C1 implementation and
does not reopen M7.
