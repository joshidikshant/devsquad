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

A schema-changing update defers while the ledger contains active or
recoverable runs. Finish, resume or cancel them with the previous release,
then retry the same installer command. Activation checks the ledger under
its lock and never advances the schema before selecting the new release.
The first new-release ledger operation performs the guarded migration;
already-open old clients cannot write after that migration commits. An
interruption before selector replacement leaves the old schema usable; an
interruption after replacement leaves migration safely retryable. Old
releases and saved receipts remain present. Custom runtime directories get
the same migration guard on first access; use `DEVSQUAD_RUNTIME_DIR` for the
installer's explicitly scoped ledger check.

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
squad doctor
squad review --base main --dry-run
squad review --base main --wait
squad fix "the bounded issue to resolve" \
  --write-path src --wait
squad status
squad finish RUN_ID --accept --reason "Reviewed the saved candidate and evidence."
squad result RUN_ID
```

Use `--dry-run` first to inspect exact commit IDs, role/profile selections,
scope and checks without creating a run or invoking a model. `squad fix`
defaults to repository-wide write scope when `--write-path` is omitted; narrow
it whenever the issue permits. `--wait` automatically advances the saved
candidate from implementation into independent review, then returns at the
host-lead handoff with the review summary, check results, evidence report and
finish command. That pause exits 2; it is saved work awaiting your assessment.
Use `--reject` or `--revise` instead of `--accept` when appropriate, with a
reason. Finish binds every artifact from the exact current packet and applies
the same claim, independent-review and required-check gates as the low-level
API. It refuses terminal replays and app-owned host claims; use the saved claim
for a handoff already owned by an app. If interrupted after acquiring its own
claim, finish saves the exact decision atomically with that claim. Status shows
the exact retry command. Only that same packet, disposition and reason can
recover the live claim or its expired fence; an owner name alone never permits
recovery. If the decision was already submitted, use `squad resume RUN_ID` to
finish the saved continuation. A claim that expires just before submission is
still rejected and audited. An exact guided retry under its own fresh valid
fence can recover that expiry: the unique submission row is an operational
projection, while an append-only recovery event preserves the complete original
rejected row and its digest. Final event exports retain both rejection and
recovery history. App claims and other rejection reasons cannot use this path.
The normal lead is the terminal host;
an explicitly configured headless run follows its existing lead through a
temporary handoff when observed with `--wait`. Omitting `--wait` returns the run
ID immediately.

Normal commands show readable output by default. Add `--json` for the versioned
automation envelope. Omitted IDs on status, result and finish resolve only when
the current canonical Git project has exactly one saved run. With no run, the
command explains how to start; with multiple runs it lists IDs and states and
requires a choice. It never selects another project's run or guesses the latest
run. Use `--project-dir PATH` when observing from outside the project.

Checks come from regular tracked files at the selected target commit. For
DevSquad this includes the Bash suite, Python core runner and generated
reference check. Delivery requires all three to pass; review-only checks are
reported even when they fail. Explicit `--check` arguments add bounded approved
commands and exact duplicates run once. A directory called `tests` is not
enough to infer a Python suite without actual tracked Python test files.

```mermaid
flowchart LR
  A[review or fix] --> B[Exact candidate review and checks]
  B --> C[Saved handoff and evidence]
  C --> D[finish with accept, reject or revise]
  D --> E[Saved receipt or bounded revision]
```

The lower-level automation path remains available. A hand-written task must
name an existing Git repository and committed refs. It may use either committed
routing files or an exact embedded registry/policy pair.

New public runs automatically record one final learning outcome, including
failed attempts and repairs. `squad report --project "$PWD" --json` includes
these without manual imports. Later feedback remains an explicit late
correction via `squad outcome add RUN --file correction.json --json`; it never
overwrites the original final outcome. Historical missing outcomes stay
missing rather than being manufactured during an upgrade.

For an explicitly reviewed, predeclared comparison, start each bounded arm:

```bash
squad trial --experiment experiment.json --case CASE --arm control \
  --task-file control-task.json --idempotency-key trial-CASE-control --wait --json
squad trial --experiment experiment.json --case CASE --arm candidate \
  --task-file candidate-task.json --idempotency-key trial-CASE-candidate --wait --json
```

This advanced automation command requires the v2 declaration (case/split,
input and concrete execution hashes, gates, budgets) before either arm. Use
branch reviews for reviewer comparisons or issue delivery for implementer
comparisons. All arms share the declared reservation/wall budget; failed and
fallback slots count. Automatic experimentation and promotion remain off.
The same `policy evaluate`, profile qualification and reviewed binding-change
commands operate on the resulting saved-run evidence; missing arms cannot
create a completed pair or authorize promotion.

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

For terminal use, `squad finish` is the supported guided disposition command.
Automation and app hosts can still use the low-level claim/complete protocol.
When a host-lead workflow pauses, JSON status returns `claim_handoff` and the
current run version. Claim and complete the saved packet without editing the claim:

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

## Manual Council (partial source implementation)

Council reuses the saved runner with two independent read-only proposers, a
distinct critic and the existing sole lead. Automatic triggering is always off;
there is one round and no internal retry. Prepare a scoped question without task
JSON:

```bash
squad council "Which retry policy avoids duplicate side effects?" \
  --read-path src/retry.py --lead host --max-invocations 3 --dry-run
```

Inspect the exact commits, read scope, rubric, checks and three catalog model
identities. Repeat `--model MODEL_ID` exactly three times to pin `proposer_a`,
`proposer_b` and critic; catalog availability is not tested quality qualification.
`--criterion ID=DESCRIPTION`, `--evidence ARTIFACT_ID:SHA256` and `--check COMMAND`
freeze explicit rubric, saved evidence and bounded checks. Preparation performs
no generation. Remove `--dry-run` only after doctor and per-run capability gates
are satisfied.

Native Council currently fails closed before any role generation:
`native_ready:false` means a genuine nongenerating backend response under the
exact default-deny macOS sandbox has not been attested. Bootstrap or cached
catalog success is not that proof. Unsupported operating systems have no unsafe
fallback. Normal review/fix remain separate. Do not broaden filesystem/MCP
access, bypass provider permissions or use a paid API to work around this gate.

Once the gated workflow is available, a host-led run pauses with shuffled A/B
proposals, criterion assessments, mandatory checks and retained dissent. Status
does not choose a proposal or infer a disposition. Finish explicitly:

```bash
squad status RUN_ID
squad finish RUN_ID --accept --choose synthesis \
  --reason "Retain the idempotency and expiry safeguards." \
  --supported-claim "Never retry a non-idempotent request blindly." \
  --discarded-alternative "Blind retry without a stable key." \
  --validation "Exercise duplicate requests and key-retention expiry."
squad result RUN_ID
```

Choose `A`, `B` or `synthesis`; repeat supported/discarded flags as needed. Every
critic objection remains in the decision. Reject with explicit assessment when
appropriate; votes cannot override failed checks. Extra deliberation requires a
new capped run, not `--revise`. `council-finish` is an explicit-choice alias;
omitted IDs follow the same unique-current-project rule as normal finish.
An interrupted guided finish exposes an exact retry command, including every
choice input. Only the latest matching canonical guided claim can recover an
expired fence; an app-owned claim, even named `terminal-operator`, cannot be
adopted. A submitted decision continues with `squad resume RUN_ID`.

The default headless lead uses four capped worker invocations, chooses only from
validated saved evidence and continues its own handoff with `--wait` or resume;
a host cannot claim it. Missing participants, invalid identities, quota,
cancellation and check failures remain failures, never fabricated consensus.
The retained predeclared matched/held-out public fixture comparison is
inconclusive and proves mechanics only: native quality, escaped defects, rework
and native allowance remain unknown. It authorizes no automatic use or model
promotion.

## Recover or cancel

Never delete the runtime database, an active release or a run-owned worktree
to recover a job. Inspect first:

```bash
squad status RUN_ID
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
squad cancel RUN_ID
squad status RUN_ID
```

For installation drift, run `scripts/install-core.sh --status --json` from the
intended checkout. Reinstalling identical content is safe. A mismatched or
corrupt content-addressed release is rejected rather than repaired in place;
install a new source digest and retain the old directory for run evidence.

## Supported and deferred boundaries

The source candidate contract is Python 3.11+, public JSON contract version 1,
SQLite schema 17 and optional MCP SDK 2.2.0 exactly. The accepted installed R5
boundary remains schema 16 until an explicitly accepted Council update; R6
source also uses epoch 16, with its own acceptance tracked separately. This
guide does not claim that source Council is installed or live accepted. Epoch 17
fences old clients that cannot understand Council handoff authority, and its
installer defers on active or recoverable schema-16 work. Native Codex fixtures and
recorded live proofs cover bundled `codex-cli 0.153.4` and
`codex-cli 0.155.0-alpha.9.2`; the latter passed a fresh native initialize and
complete model-catalog probe. The resolver prefers that verified bundled
binary over an older unverified PATH binary. The Claude worker adapter is
version-scoped to CLI 2.1.220. Antigravity 1.2.13 has a live read-only MCP
receipt; later installed CLI/MCP receipts cover Grok 1.0.46. Check the current
doctor report rather than treating an older receipt as proof for a new version.
Version changes are capability drift and require a fresh conformance probe;
brand names are not a compatibility promise.

Implemented surfaces and evidence:

| Surface | Current evidence |
|---|---|
| Terminal | Standalone install and real saved-run cancellation; fresh installed normal review/fix/finish flow verified with offline provider binaries |
| Codex App/CLI | Matching MCP registration and a fresh installed-runtime `squad_status` receipt on 0.155.0-alpha.9.2 |
| Claude Code local Code tab | Matching registration and real Claude MCP handoff; local Code-tab UI proof remains open |
| Antigravity CLI (`agy`) | Matching registration and a live Gemini `squad_status` receipt with one project-scoped grant; IDE is outside the clarified request |
| Grok Build | Matching registration and real Grok 1.0.46 MCP status operation |

The historical installed surface evidence source is
[`M7-installed-runtime-2026-09-29.json`](plans/engineering-team/evidence/M7-installed-runtime-2026-09-29.json).
The installed normal-entry evidence is
[`M7-normal-entry-2026-09-29.json`](plans/engineering-team/evidence/M7-normal-entry-2026-09-29.json).
Later verified runtime proofs, including the accepted two-model delivery,
actual Claude handoff, Grok MCP and Gemini CLI/MCP recheck, are recorded in
[`R8-installed-workflows-2026-10-01.json`](plans/engineering-team/evidence/R8-installed-workflows-2026-10-01.json).
These are operation-scoped receipts; the Claude local Code-tab UI proof and
remaining R6/R8 acceptance gates remain open. Antigravity acceptance uses the
CLI, not IDE trust or UI. Doctor separates installed binaries, supported
adapter versions, non-generating authentication checks, registrations and
operation verification. Unknown verification stays unknown. A CLI that is
installed but unsupported or missing subscription authentication does not make
review/fix ready. Complete login through the named provider's normal flow.

Portable task files, handoff packets, event ledgers and hashed artifacts are
the cross-host interface. A reviewed upstream Codex integration demonstrates
optional native Claude-to-Codex transcript import, but DevSquad does not yet
expose or test that import path. It is deferred and must never be substituted
for the portable handoff contract or described as general chat-history
transfer.

The one authorized Jev pilot is recorded separately and runtime classification
remains off; normal routing uses the deterministic zero-call path. Laya is not
installed unless its declared trigger fires. C1 Council is implemented partially
in source with offline process/isolation/epoch proofs; native backend attestation,
live quality and final installed acceptance remain open.
