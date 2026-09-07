# Resume DevSquad after an interruption

This file is the recovery entry point for a quota cutoff, interrupted task or new coding-agent session. Update it at each coherent checkpoint and before a long live probe. A pending milestone stays pending when its evidence is incomplete.

## Current position — September 7, 2026

- Workspace: `/Users/Dikshant/Desktop/Projects/devsquad`.
- Build branch: `codex/engineering-team`. `main` is the published runtime baseline.
- Last verified implementation checkpoint: `a67ab58`.
- Last evidence/status checkpoint before this recovery note: `cc98ce7`.
- GitHub build branch contains the cleanup/architecture checkpoint `55e93a2`; later implementation checkpoints are local. Inspect the actual current refs before acting.
- User wants **Sol to implement, with Astra reviewing**, and explicitly wants work preserved across Plus-plan usage interruptions.
- Full assignment remains **M1–M7 plus C1**, as specified in [SOL-HANDOFF.md](SOL-HANDOFF.md). M2 has not started.

## Completed and preserved

Branch cleanup is complete. Local/GitHub working branch names were consolidated into `main` and `codex/engineering-team`. The old assessment and holdout commits remain in their descendant histories. The unrelated February backup is preserved by its existing local tag and a verified complete Git bundle. See the [branch record](../../audits/2026-09-06-branch-consolidation.md).

The M1 implementation includes Python packaging/contracts, native Codex protocol preparation and framing, catalog-to-profile preparation, shared error-classification policy, strict input validation, and legacy timeout/context/catalog fixes. Earlier review defects have corresponding regression tests, including [the independent review cases](../../../test/core/test_m1_gate_review.py).

Verified at the implementation/evidence checkpoints above:

| Check | Result |
|---|---|
| Python core discovery | 32 tests passed |
| Bash 3.2 regression suite | 10 test files, 202 assertions passed |
| Wheel installation | Temporary venv resolves packaged schemas, adapters and shared taxonomy |
| Earlier live probes | Codex metadata and a separate read-only CLI smoke succeeded |
| Integrated native adapter proof | **Still pending** |

The authoritative requirement matrix is [M1-STATUS.md](M1-STATUS.md); detailed evidence is [M1-invocation-core-2026-09-06.json](evidence/M1-invocation-core-2026-09-06.json). [backlog.json](backlog.json) retains M1 as `in_progress`. Grok workspace-write and unprobed Antigravity/Grok settings are not advertised as verified.

## Exact next work

1. Check Git state; preserve any new changes before doing further work. Read this file, M1-STATUS and the full Sol handoff. Do not restart the architecture exercise or reset to `main`.
2. Save a reproducible, explicitly opt-in native probe script **before** running it. Previous integrated probes were inline scripts and have no retained raw logs, so their reported failures cannot yet be independently diagnosed.
3. Exercise the actual path: initialized app-server → complete model discovery → saved snapshot → prepared LaunchSpec → native thread/turn → correlated terminal result. Use one bounded read-only inference, temporary workspace and existing subscription authentication. Save a redacted result plus private local diagnostic files.
4. Investigate the nonresponse before declaring an external blocker. Prior probes reported successful initialization but no `model/list` or `thread/start` reply after bounded waits, despite sending the required `initialized` notification. They reported an installed Codex 0.135.0 warning about the global `ultra` effort value. Per-process overrides were attempted; global settings were not changed. **A new hypothesis to test is blocked stderr output from an undrained subprocess PIPE.** Redirect stderr to a private file or drain it concurrently; a full stderr pipe can stall a child. This cause is not yet established.
5. If the actual adapter path succeeds, record the exact revision/commands/outcome and complete the remaining independent M1 review. Only then close M1 and proceed to M2. If it fails, retain the exact diagnostics and keep the live gate open; distinguish implementation defects from provider/configuration limitations.

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
