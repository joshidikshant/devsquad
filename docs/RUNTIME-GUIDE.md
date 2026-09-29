# DevSquad runtime guide

This guide covers the surface-independent Python runtime. The older Claude
plugin remains available, but it is not required for the standalone command.

## Install and update

Prerequisites are macOS or another Unix-like host and Python 3.11 or newer.
The base runtime has no third-party Python dependencies and installation does
not contact a provider or package index.

From a DevSquad checkout:

```bash
./install.sh --core-only
export PATH="$HOME/.local/bin:$PATH"
squad --version
./scripts/install-core.sh --status --json
```

`scripts/install-core.sh` creates a Python virtual environment and an exact
copy of `plugin/core` under an immutable, content-addressed directory in
`~/.devsquad/releases/`. `~/.devsquad/current` is changed atomically and
`~/.local/bin/squad` remains stable. A second identical install reports
`"changed":false`. The status report compares the source, plugin and actual
installed payload digests; it does not trust the release manifest alone.

`./install.sh` also installs or updates the legacy Claude plugin when the
`claude` command exists. Use `--with-claude` to require that path. The plugin's
`hooks/hooks.json` is the only hook registration written by the installer;
the composite installer does not add another copy to global settings.

The optional local MCP bridge is isolated from the dependency-free base. It
uses the exact `mcp==2.2.0` lock and never downloads implicitly. Prepare a
wheelhouse containing every package in `plugin/core/requirements-mcp.lock`,
then run:

```bash
./scripts/install-core.sh --with-mcp --mcp-wheelhouse /absolute/path/to/wheels
squad setup --dry-run --json
squad setup --json
squad doctor --json
```

Setup registers only one stable `squad` launcher. It refuses duplicate,
inherited, malformed or ambiguous MCP registrations instead of guessing which
one to replace. An upgrade retains every previous release, so a process that
started before the selector changed can finish against its frozen package.

## Operate a run

The generated [command and schema reference](generated/core-reference.md)
lists every command form, packaged schema digest and a strict task-shape
example. A real task must name an existing Git repository, committed refs and
committed routing files containing locally verified profiles.

```bash
squad start --task-file task.json --idempotency-key issue-123 --json
squad status RUN_ID --json
squad events RUN_ID --after 0 --limit 100 --json
squad result RUN_ID --json
```

Keep the returned run ID. Reusing an idempotency key with an identical request
returns the original run; reusing it with different content conflicts. Status
is the authority for the current state and next action. Result artifacts and
their SHA-256 values are authoritative; a chat summary is not.

When a host-lead workflow pauses, status returns `claim_handoff` and the
current run version. Claim and complete the saved packet without editing the
claim:

```bash
squad handoff claim RUN_ID --expected-version VERSION --owner local-operator --json > claim-response.json
python3 -c 'import json,sys; json.dump(json.load(sys.stdin)["data"]["claim"],sys.stdout)' < claim-response.json > claim.json
squad handoff complete RUN_ID --claim-file claim.json --decision-file decision.json --json
```

The initial claim command returns the claim; `--claim-file` on that command is
only for renewing an existing claim. Construct `decision.json` from the exact
packet and evidence references returned with the claim, following the strict
[handoff contract](plans/engineering-team/CONTRACTS.md).

Every surface uses these operations directly or through the thin local MCP
bridge described in [MCP-LOCAL-ACCESS.md](plans/engineering-team/MCP-LOCAL-ACCESS.md).
Closing an app does not cancel the detached run.

## Recover or cancel

Never delete the runtime database, an active release or a run-owned worktree
to recover a job. Inspect first:

```bash
squad status RUN_ID --json
squad events RUN_ID --after 0 --limit 100 --json
```

If status supplies a recovery object, save that exact object as
`recovery.json`, reconcile the process identity it describes, and then run:

```bash
squad resume RUN_ID --recovery-file recovery.json --json
```

If status does not request recovery, do not invent a recovery decision.
Cancellation is durable and idempotent:

```bash
squad cancel RUN_ID --json
squad status RUN_ID --json
```

For installation drift, run `scripts/install-core.sh --status --json` from the
intended checkout. Reinstalling identical content is safe. A mismatched or
corrupt content-addressed release is rejected rather than repaired in place;
install a new source digest and retain the old directory for run evidence.

## Supported and deferred boundaries

The current packaged contract is Python 3.11+, public JSON contract version 1,
SQLite schema 13 and optional MCP SDK 2.2.0 exactly. Native Codex fixtures and
the recorded live proof cover bundled `codex-cli 0.153.4`. The Claude worker
adapter is version-scoped to CLI 2.1.220. Antigravity 1.2.3 and Grok 0.2.111
have registration evidence but their live worker paths remain blocked by
trust/permission and expired authentication respectively. Version changes are
capability drift and require a fresh conformance probe; brand names are not a
compatibility promise.

Implemented surfaces and evidence:

| Surface | Current evidence |
|---|---|
| Terminal | Standalone install, start/status/result/cancel/recovery and update-survival tests |
| Codex App/CLI | Matching MCP registration and a real saved-run `squad_status` receipt |
| Claude Code local Code tab | Matching registration; live operation blocked on normal provider login |
| Antigravity local IDE/CLI | Matching registration; live scoped operation blocked on trust/permission |
| Grok Build | Matching registration; live operation blocked on expired authentication |

The evidence source is
[`M4-local-mcp-2026-09-23.json`](plans/engineering-team/evidence/M4-local-mcp-2026-09-23.json).
M7 requires new installed-runtime receipts before claiming universal surface
support.

Portable task files, handoff packets, event ledgers and hashed artifacts are
the cross-host interface. A reviewed upstream Codex integration demonstrates
optional native Claude-to-Codex transcript import, but DevSquad does not yet
expose or test that import path. It is deferred and must never be substituted
for the portable handoff contract or described as general chat-history
transfer.

The optional Jev decision probe remains blocked until `TYPESAFE_API_KEY` is
provided; normal routing defaults to the deterministic zero-call path. Laya is
not installed unless the declared Jev fallback trigger fires. The optional C1
Council extension is also deferred and does not block the core runtime.
