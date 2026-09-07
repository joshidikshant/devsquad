# Resume DevSquad after an interruption

This file is the recovery entry point for a quota cutoff, interrupted task or new coding-agent session. Update it at each coherent checkpoint and before a long live probe. A pending milestone stays pending when its evidence is incomplete.

## Current position — September 7, 2026

- Workspace: `/Users/Dikshant/Desktop/Projects/devsquad`.
- Build branch: `codex/engineering-team`. `main` is the published runtime baseline.
- Accepted M1 implementation checkpoint: `97a10f0`.
- Probe cleanup hardening checkpoint: `7b5c41c`.
- Accepted M1 status/evidence checkpoint: `c50c6b4`.
- Native correlation/probe checkpoint: `97a10f0`. The saved integrated probe
  passed; its private receipt SHA256 is
  `324c2ce6154936ecf71a8bf913a195befd4251efc6dbb8bbab3dc0dbe1b86df8`.
  Exact private receipt directory:
  `/Users/Dikshant/.devsquad/private-probes/native-codex-20260907T025419Z-97a10f0ae1e8`.
- M1 is accepted for its bounded invocation/preparation scope. The next
  milestone is M2.
- M2 store foundation checkpoint: `0c2929c`. M2 remains in progress; its
  supervisor, service operations and recovery slices are not implemented.
- GitHub build branch contains the cleanup/architecture checkpoint `55e93a2`; later implementation checkpoints are local. Inspect the actual current refs before acting.
- User wants **Sol to implement, with Astra reviewing**, and explicitly wants work preserved across Plus-plan usage interruptions.
- Full assignment remains **M1–M7 plus C1**, as specified in [SOL-HANDOFF.md](SOL-HANDOFF.md). M2 has not started.

## Completed and preserved

Branch cleanup is complete. Local/GitHub working branch names were consolidated into `main` and `codex/engineering-team`. The old assessment and holdout commits remain in their descendant histories. The unrelated February backup is preserved by its existing local tag and a verified complete Git bundle. See the [branch record](../../audits/2026-09-06-branch-consolidation.md).

The M1 implementation includes Python packaging/contracts, native Codex protocol preparation and framing, catalog-to-profile preparation, shared error-classification policy, strict input validation, and legacy timeout/context/catalog fixes. Earlier review defects have corresponding regression tests, including [the independent review cases](../../../test/core/test_m1_gate_review.py).

Verified at the implementation/evidence checkpoints above:

| Check | Result |
|---|---|
| Python core discovery | 34 tests passed |
| Bash 3.2 regression suite | 10 test files, 202 assertions passed |
| Wheel installation | Temporary venv resolves packaged schemas, adapters and shared taxonomy |
| Earlier live probes | Codex metadata and a separate read-only CLI smoke succeeded |
| Integrated native adapter proof | Passed at `97a10f0`; gpt-5.5/low, read-only, correlated completion and confirmed process-group cleanup |

The first two saved-probe invocations failed before `Popen` because of
probe-only path/field defects, so neither launched Codex nor consumed a model
turn. Their private receipts remain under `~/.devsquad/private-probes`. The
probe now uses a dedicated process session, bounded group TERM/KILL cleanup,
an explicit terminal deadline, and retains early notifications for correlation.
The successful run retained separate stderr files of 138,030 and
285,644 bytes, supporting the diagnosis that an undrained stderr pipe caused
the earlier apparent nonresponses.

The authoritative requirement matrix is [M1-STATUS.md](M1-STATUS.md); detailed evidence is [M1-invocation-core-2026-09-06.json](evidence/M1-invocation-core-2026-09-06.json). [backlog.json](backlog.json) marks M1 complete and M2 next. Grok workspace-write and unprobed Antigravity/Grok settings are not advertised as verified.

## Exact next work

1. Check Git state; preserve any new changes before doing further work. Read this file, M1-STATUS and the full Sol handoff. Do not restart the architecture exercise or reset to `main`.
2. Read the M2 section of [IMPLEMENTATION.md](IMPLEMENTATION.md),
   [M2-STATUS.md](M2-STATUS.md), and the corresponding contracts before
   editing. Continue with the bounded supervisor/recovery assignment from
   root; do not redo the accepted store foundation.
3. Preserve M1 limitations and process-ownership boundaries. M1 acceptance is
   not a claim that the full product exists, and no additional native probe is
   needed for the accepted gate.

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
