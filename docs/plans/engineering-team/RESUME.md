# Resume DevSquad after an interruption

This file is the recovery entry point for a quota cutoff, interrupted task or new coding-agent session. Update it at each coherent checkpoint and before a long live probe. A pending milestone stays pending when its evidence is incomplete.

## Current position — September 16, 2026

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
- The next milestone is M3. Public `branch-review` still fails explicitly as
  `CAPABILITY_UNAVAILABLE`; M2 is infrastructure, not yet a usable engineering
  workflow. Do not advertise DevSquad as ready for real review until M3 passes.
- Current provider readiness is external to M2: Claude CLI is not logged in;
  Grok CLI authentication expired; Gemini CLI's individual-account path is
  unsupported and its supported successor is Antigravity; Antigravity is
  authenticated but headless execution still lacks scoped permission/trust.
  Do not retry these blocked paths, buy credits, use paid API fallback or
  change global provider settings. Resume a provider gate only after the user
  completes the corresponding normal login/permission action.
- User wants the implementation orchestrated efficiently and preserved across
  Plus-plan interruptions. Avoid recursive subagent fan-out: it consumed the
  shared window rapidly without advancing M3.
- Full assignment remains **M1–M7 plus C1**, as specified in
  [SOL-HANDOFF.md](SOL-HANDOFF.md). M3 is next.

## Completed and preserved

Branch cleanup is complete. Local/GitHub working branch names were consolidated into `main` and `codex/engineering-team`. The old assessment and holdout commits remain in their descendant histories. The unrelated February backup is preserved by its existing local tag and a verified complete Git bundle. See the [branch record](../../audits/2026-09-06-branch-consolidation.md).

The M1 implementation includes Python packaging/contracts, native Codex protocol preparation and framing, catalog-to-profile preparation, shared error-classification policy, strict input validation, and legacy timeout/context/catalog fixes. Earlier review defects have corresponding regression tests, including [the independent review cases](../../../test/core/test_m1_gate_review.py).

Verified at the implementation/evidence checkpoints above:

| Check | Result |
|---|---|
| Python core discovery | 121 tests passed at the M2 acceptance gate |
| Bash 3.2 regression suite | 10 test files, 202 assertions passed |
| Wheel installation | Fresh external venv resolves packaged assets and applies migrations through schema 5 |
| Earlier live probes | Codex metadata and a separate read-only CLI smoke succeeded |
| Integrated native adapter proof | Passed at `97a10f0`; gpt-5.5/low, read-only, correlated completion and confirmed process-group cleanup |
| M2 crash/race matrix | Real subprocess interruptions plus independent-process start, writer, cancel, import and host-handoff races passed at `ddb6f51` |

The first two saved-probe invocations failed before `Popen` because of
probe-only path/field defects, so neither launched Codex nor consumed a model
turn. Their private receipts remain under `~/.devsquad/private-probes`. The
probe now uses a dedicated process session, bounded group TERM/KILL cleanup,
an explicit terminal deadline, and retains early notifications for correlation.
The successful run retained separate stderr files of 138,030 and
285,644 bytes, supporting the diagnosis that an undrained stderr pipe caused
the earlier apparent nonresponses.

The authoritative requirement matrices are [M1-STATUS.md](M1-STATUS.md) and
[M2-STATUS.md](M2-STATUS.md). [backlog.json](backlog.json) marks both complete
and M3 next. Unauthenticated, unsupported or permission-blocked provider paths
are not advertised as verified.

## Exact next work

1. Check Git status and recent commits, preserving work newer than this note.
   Continue `codex/engineering-team`; do not restart from `main` or redo M1/M2.
2. Read the M3 section of [IMPLEMENTATION.md](IMPLEMENTATION.md), the task,
   Profile/Policy and workflow sections of [CONTRACTS.md](CONTRACTS.md), and
   the selection amendment. Implement the earliest M3 dependency: strict
   profile/policy loading plus deterministic frozen role bindings and fallback
   decisions, with tests. Then add frozen review workspace preparation before
   any real reviewer launch.
3. Preserve the M2 process/fencing boundaries. Extend the durable runner with
   real workflow steps; do not bypass it with an in-process or ad-hoc provider
   call. Keep public review unavailable until reviewer, declared checks, lead
   disposition and bound receipt artifacts form a valid end-to-end slice.
4. Use offline fake adapters for development. Provider login/permission work is
   a later live gate and must not block independent M3 implementation.

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
