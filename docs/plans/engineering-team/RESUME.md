# Resume DevSquad after an interruption

This file is the recovery entry point for a quota cutoff, interrupted task or new coding-agent session. Update it at each coherent checkpoint and before a long live probe. A pending milestone stays pending when its evidence is incomplete.

## Current position — September 23, 2026

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
- M3 is accepted at `1737667`. The branch-review path now includes frozen
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
- M4 Plan 06-02 is complete at `9abad1e`. Four packaged host templates now
  drive duplicate-aware `squad setup`; `squad doctor` and the ninth MCP tool,
  `squad_doctor`, report the app-loaded command, SDK and registration drift
  without returning environment maps or arbitrary arguments. Registration is
  fail-closed for inherited/duplicate/malformed config and is idempotent while
  preserving unrelated settings. The gate is 211 core tests with
  `ResourceWarning` promoted to failure, 208 Bash assertions and 21 focused
  tests under the pinned SDK. Installed Codex 0.135.0, Claude 2.1.220,
  Antigravity 1.2.3 and Grok 0.2.111 CLIs each passed add, second-run no-op and
  drift-repair checks under an isolated temporary HOME. That is CLI/config
  evidence, not a claim of in-app operation; real account configs remain
  untouched. M4 remains in progress for Plan 06-03 cross-surface receipts.
- Last-observed provider readiness outside accepted M3: Claude CLI is not logged in;
  Grok CLI authentication expired; Gemini CLI's individual-account path is
  unsupported and its supported successor is Antigravity; Antigravity is
  authenticated but headless execution still lacks scoped permission/trust.
  Do not retry these blocked paths, buy credits, use paid API fallback or
  change global provider settings. Resume a provider gate only after the user
  completes the corresponding normal login/permission action.
- User wants the implementation orchestrated efficiently and preserved across
  Plus-plan interruptions. Avoid recursive subagent fan-out: it consumed the
  shared window rapidly without advancing M3. The recursively created M3
  planning agents all hit the same Plus limit; continue locally until shared
  agent capacity is restored, then use only bounded leaf reviews.
- Full assignment remains **M1–M7 plus C1**, as specified in
  [SOL-HANDOFF.md](SOL-HANDOFF.md). M4 is next; M5 may proceed after the frozen
  M3 service boundary and can be developed alongside M4 in isolated slices.

## Completed and preserved

Branch cleanup is complete. Local/GitHub working branch names were consolidated into `main` and `codex/engineering-team`. The old assessment and holdout commits remain in their descendant histories. The unrelated February backup is preserved by its existing local tag and a verified complete Git bundle. See the [branch record](../../audits/2026-09-06-branch-consolidation.md).

The M1 implementation includes Python packaging/contracts, native Codex protocol preparation and framing, catalog-to-profile preparation, shared error-classification policy, strict input validation, and legacy timeout/context/catalog fixes. Earlier review defects have corresponding regression tests, including [the independent review cases](../../../test/core/test_m1_gate_review.py).

Verified at the implementation/evidence checkpoints above:

| Check | Result |
|---|---|
| Python core discovery | 211 tests passed through M4 Plan 06-02 with warnings promoted to errors |
| Bash 3.2 regression suite | 10 test files, 208 assertions passed |
| Optional MCP boundary | `mcp==2.2.0` installed/constructed on local Python; Python 3.11 lock resolution; 21 official-SDK focused tests passed |
| M4 local host setup | Four installed host CLIs registered under an isolated HOME, repaired drift and made no second-run changes; duplicate/inherited configs fail closed |
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
[M2-STATUS.md](M2-STATUS.md) and [M3-STATUS.md](M3-STATUS.md).
[backlog.json](backlog.json) marks all three complete and M4 next.
Unauthenticated, unsupported or permission-blocked provider paths are not
advertised as verified.

## Exact next work

1. Check Git status and recent commits, preserving work newer than this note.
   Continue `codex/engineering-team`; do not restart from `main` or redo M1/M2.
2. Execute M4 Plan 06-03: install one stable isolated local MCP runtime, use
   the idempotent setup path for the available host surfaces, and prove one
   saved run across terminal and Codex with client-disconnect survival,
   handoff fencing, duplicate detection and installed-version receipts.
3. Keep Claude/Grok/Antigravity probes paused until their normal login or trust
   blockers are resolved. They do not block the independent Codex M3 gate.

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
