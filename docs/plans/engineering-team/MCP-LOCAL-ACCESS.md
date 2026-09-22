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

The next M4 slice implements duplicate-aware `squad setup` and doctor output
over these templates. Until that checkpoint, the templates are tested package
data and a registration specification, not a claim that any host is already
configured or connected.
