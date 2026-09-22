# Local MCP access — operator contract

This is the minimal M4 host contract for the shared local DevSquad runtime. It
does not create a cloud service, select a model for the host, or copy routing
policy into app-specific prompts.

## One durable run, interchangeable clients

Every supported local app launches the same absolute executable over stdio:

```text
/absolute/path/to/squad mcp serve --surface HOST
```

The four versioned templates are under `plugin/core/integrations/`. Setup
resolves both the host CLI and `squad` to existing absolute files before it
registers anything. Host labels are saved as provenance only; they never grant
authorization. Worker/delegation environment markers, not a caller-supplied
label, deny recursive workflow mutations.

Use the tools in this order:

1. Construct one strict v1 task with bounded criteria, scope, checks and
   budgets. Call `squad_start` once with a stable idempotency key and retain the
   returned run ID.
2. Use `squad_status` for the projection and next action. Page
   `squad_events` with `after=next_cursor`; do not treat provider reasoning as
   an event stream.
3. Use `squad_result` for receipt/artifact IDs, paths and hashes. Its optional
   UTF-8 previews share one 16 KiB ceiling; the files remain the authority.
4. If status reports `claim_handoff`, call `squad_handoff_claim` with the
   displayed run version and a stable local owner label. Submit the returned
   claim unchanged with a candidate-bound decision to
   `squad_handoff_complete`.
5. `squad_cancel` saves cancellation intent; it does not wait for a worker's
   lifetime. Use `squad_resume` only with the recovery object requested by
   status.

Closing an app or its MCP client does not cancel a saved run. The detached
worker is owned by the machine-local runtime, and another client can continue
with the same run ID. Two hosts still cannot advance the same handoff because
the service checks its fencing token and run version.

## Registration templates

| Host | CLI used by setup | Scope | Inspection form |
|---|---|---|---|
| Codex App/CLI | `codex mcp add` | Codex user config | `codex mcp get ... --json` |
| Claude Code | `claude mcp add --scope user` | user | `claude mcp get` |
| Antigravity | `agy mcp add` | Antigravity user config | `agy mcp list` |
| Grok Build | `grok mcp add --scope user` | user | `grok mcp list --json` |

Install the optional, exactly pinned MCP extra into the same stable environment
that owns the `squad` executable, then preview or apply registration:

```text
python3 -m pip install './plugin/core[mcp]'
squad setup --dry-run --json
squad setup --json
squad doctor --json
```

Use `--host codex`, `--host claude-code`, `--host antigravity` or
`--host grok` to limit setup; repeat `--host` for more than one. Setup calls
the installed host CLI with argument arrays, never a shell command, and then
re-inspects what that host reports as loaded. A second successful setup is a
no-op. Claude Code requires a targeted user-scope remove/add only when its
existing direct `devsquad` entry has drifted because its CLI does not replace a
named server in place.

Setup fails closed instead of editing an ambiguous configuration when it sees:

- the server in more than one direct scope;
- a loaded registration that differs from the one visible in the direct
  config, indicating an inherited override;
- a project/local registration, malformed config or a config the host does
  not load;
- an unresolved launcher, unavailable host CLI, missing SDK or any MCP SDK
  version other than the supported `2.2.0` pin.

`squad doctor --json` is read-only. It reports adapter versions, the resolved
launcher, installed SDK version, config paths, normalized host inspection and
whether each installed app loads the expected absolute command. Environment
maps are never returned. Arguments are returned only when they exactly match
the fixed DevSquad server arguments; drifted arguments are replaced by a count
and SHA-256 digest so credentials cannot be echoed. Doctor exits 1 when an
installed app is not ready, while unavailable apps are not treated as required.

These commands prove local CLI registration and SDK conformance. Actual
in-app operation still requires the cross-surface receipts in M4/M7; config
syntax or a matching `mcp list` result is not presented as that live proof.
