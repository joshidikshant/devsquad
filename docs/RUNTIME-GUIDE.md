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

Antigravity's non-interactive print mode also enforces project permissions.
For unattended read-only status checks, add this exact grant to the DevSquad
project's Permissions list in Antigravity:

```text
mcp(devsquad/squad_status)
```

This is narrower than a server wildcard and does not authorize terminal or
file access. Interactive use may instead approve the requested MCP operation
when prompted. Do not use the blanket permission-bypass option for setup or a
smoke test.

## Operate a run

The generated [command and schema reference](generated/core-reference.md)
lists every command form, packaged schema digest and a strict task-shape
example. Normal review and fix entry resolves committed refs, discovers the
installed Codex catalog without a generation, freezes bounded subscription
profiles and embeds the validated routing snapshot. It does not require task,
profile or policy JSON:

```bash
squad review --base main --wait --json
squad fix "the bounded issue to resolve" \
  --write-path src --check "bash test/run.sh" --wait --json
```

Use `--dry-run` first to inspect exact commit IDs, role/profile selections,
scope and checks without creating a run or invoking a model. `squad fix`
defaults to repository-wide write scope when `--write-path` is omitted; narrow
it whenever the issue permits. `--wait` automatically advances the saved
candidate from implementation into independent review, then returns at the
host-lead handoff. Omitting `--wait` returns the run ID immediately.

The lower-level automation path remains available. A hand-written task must
name an existing Git repository and committed refs. It may use either committed
routing files or an exact embedded registry/policy pair.

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
recorded live proofs cover bundled `codex-cli 0.153.4` and
`codex-cli 0.155.0-alpha.9.2`; the latter passed a fresh native initialize and
complete model-catalog probe. The resolver prefers that verified bundled
binary over an older unverified PATH binary. The Claude worker adapter is
version-scoped to CLI 2.1.220. Antigravity 1.2.13 has a live read-only MCP
receipt; Grok 0.2.111 has matching registration but expired authentication.
Version changes are capability drift and require a fresh conformance probe;
brand names are not a compatibility promise.

Implemented surfaces and evidence:

| Surface | Current evidence |
|---|---|
| Terminal | Standalone install plus a real saved-run start and terminal cancellation |
| Codex App/CLI | Matching MCP registration and a fresh installed-runtime `squad_status` receipt on 0.155.0-alpha.9.2 |
| Claude Code local Code tab | Matching registration; live operation blocked on normal provider login |
| Antigravity local IDE/CLI | Matching registration and a live Gemini `squad_status` receipt with one project-scoped grant |
| Grok Build | Matching registration; live operation blocked on expired authentication |

The current evidence source is
[`M7-installed-runtime-2026-09-29.json`](plans/engineering-team/evidence/M7-installed-runtime-2026-09-29.json).
The installed normal-entry evidence is
[`M7-normal-entry-2026-09-29.json`](plans/engineering-team/evidence/M7-normal-entry-2026-09-29.json).
Claude, Grok and the installed two-model delivery remain open, so universal
surface support is not yet claimed.

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
