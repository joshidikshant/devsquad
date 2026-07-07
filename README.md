# DevSquad

**An Engineering Manager for your AI coding agents.**

DevSquad is a [Claude Code](https://docs.anthropic.com/en/docs/claude-code) plugin that turns Claude into an Engineering Manager, coordinating a squad of external AI coding CLIs — **Antigravity** (Gemini/Claude/GPT-OSS models), **Codex** (GPT), and **Grok Build** (xAI) — through **hook-based delegation**. Advisory suggestions by default; an opt-in strict mode denies intercepted tool calls when a delegation target is installed.

Instead of Claude doing everything itself and burning through its context window, DevSquad intercepts tool usage, suggests routing bulk work to the right CLI, and tracks usage across the whole squad. Delegation pressure is driven by two separate, honestly-named signals: **context occupancy** of the current session (measured from the transcript) and **daily output volume** (a budget signal). Token-savings figures shown in suggestions are heuristics, not measurements — and whether delegation actually pays is under live experiment (see [The D1 experiment](#the-d1-experiment)).

> **A note on honesty.** This README describes what the code does, not an aspiration. The default is advisory (i.e. suggestions); strict mode degrades to advisory when a CLI is missing or `jq` is absent. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full picture and [CHANGELOG.md](CHANGELOG.md) for how it got here.

## The squad

| Role | CLI | Best at |
|------|-----|---------|
| `gemini-*` | **Antigravity** (`agy`) | Bulk reading (1M context), research, code. Multiplexes Gemini 3.5 Flash / 3.1 Pro / Claude Sonnet & Opus 4.6 / GPT-OSS behind one CLI. |
| `codex-*` | **Codex** (`codex`) | Scaffolding, test generation (GPT models). |
| `grok-*` | **Grok Build** (`grok`) | Live web/X research (current events, sentiment), drafts. Full agent session per call (~2–5 min latency). |
| synthesis | **Claude** (self) | Architecture, integration, final judgment — never delegated. |

> The open-source Gemini CLI was **decommissioned by Google on 2026-06-18**. DevSquad's `gemini` role is served by the Antigravity CLI only; a legacy `gemini` binary on PATH no longer counts as available.

## Why?

CLAUDE.md instructions are ignorable. Hooks are not. After many sessions of documentation-based delegation rules being ignored, DevSquad replaced them with **runtime hooks** that intercept tool calls at the source.

That premise is itself under test — DevSquad measures whether delegation nets positive rather than assuming it. See [The D1 experiment](#the-d1-experiment).

## Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (CLI)
- **Antigravity CLI** — `brew install --cask antigravity-cli` (provides `agy`; authenticates via the Antigravity IDE login)
- **Codex CLI** — `npm install -g @openai/codex` (optional; graceful degradation)
- **Grok Build CLI** — see https://grok.com/build (optional; run `grok login` once)
- `jq` — for JSON processing (`brew install jq`)

All external CLIs are optional and degrade gracefully; DevSquad only suggests delegating to CLIs that are actually installed and authenticated.

## Installation

```bash
git clone https://github.com/joshidikshant/devsquad.git
cd devsquad && bash install.sh
```

Or manually:

```bash
claude plugin marketplace add https://github.com/joshidikshant/devsquad.git
claude plugin install devsquad@devsquad-marketplace
```

After installing, restart Claude Code and run `/devsquad:setup` in each project where you want enforcement active.

> **Hook registration.** `install.sh` registers hooks into `~/.claude/settings.json` pointing at the **marketplace clone** — a git checkout that `claude plugin marketplace update` refreshes. Never point hook commands at a versioned cache dir; that freezes hooks at install-time and silently drops later fixes. After pulling a new release, run `claude plugin update devsquad@devsquad-marketplace`. Details in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#deployment-modes-the-drift-trap).

## Usage

### Slash commands

| Command | Description |
|---------|-------------|
| `/devsquad:setup` | Onboarding — detect environment, set preferences, generate config |
| `/devsquad:config` | View or edit delegation preferences (e.g. `enforcement_mode=strict`) |
| `/devsquad:status` | Squad health, token usage, delegation stats, budget zone |
| `/devsquad:models` | Live model catalog, tier resolutions, and model drift |
| `/devsquad:capacity` | Report CLI usage percentages for capacity-aware delegation |
| `/devsquad:git-health` | Scan repo for broken symlinks, orphaned branches, uncommitted changes |
| `/devsquad:generate <desc>` | Generate a new DevSquad skill (Gemini research → Codex draft → review) |
| `/devsquad:workflow` | Run a multi-step workflow from a JSON definition |

### How it works

1. **Session starts** → `session-start` hook detects CLIs, initializes state, and refreshes the model catalog in the background if stale.
2. **You work normally** → Claude handles requests as usual.
3. **Hook intercepts** → `pre-tool-use` fires on Read / WebSearch / test-Bash / Task.
4. **Signals + threshold** → After ~20 file reads in a session (8 under measured context pressure), Claude is advised to delegate bulk reading; WebSearch is always advised. At most 3 suggestions are injected per session (a back-off cap so the plugin never spends more context than it saves).
5. **Agent executes** → A thin Claude subagent shell exports its name and calls the wrapper; the shared adapter invokes the external CLI with bounded execution, rate-limit cooldown, and auth-before-rate error classification.
6. **Usage tracked** → Every invocation is recorded; response bounds ("Under 300 words") are checked into a contract log; acceptance is tracked precisely (a matching `Task` call = accepted; same tool again = declined; anything else = unresolved).

### Model selection & tier pins (churn-proof)

Antigravity and Grok rotate models frequently. Rather than pin exact names (which go stale — `agy` silently ignores unknown model names), pin **intent**:

```bash
/devsquad:config agent_models.gemini-reader=tier:fast       # newest flash-class model
/devsquad:config agent_models.gemini-researcher=tier:frontier  # highest-version pro/opus
```

`tier:fast` / `tier:frontier` resolve at invocation time against a machine-local model catalog (`~/.devsquad/models.json`, auto-refreshed when >24h stale). When a provider ships a new model, tier pins follow it with zero config edits; exact-name pins still work and are validated at set time. `/devsquad:models` shows the catalog and what each tier currently resolves to. Per-agent models fall back to `preferences.<cli>_model`, then the CLI default.

### Enforcement modes

| Mode | Behavior |
|------|----------|
| `advisory` (default) | Suggests delegation; Claude may proceed anyway. |
| `strict` | Denies the intercepted tool call, requiring delegation — with availability-safe fallback to advisory when the target CLI or `jq` is missing. |

## The D1 experiment

DevSquad's core claim — that delegation saves Claude tokens net of overhead — is **falsifiable and under test**, not assumed. With `holdout_mode=true`, sessions split by a session-id hash: half are **control** (suggestions suppressed, logged identically) and half **treatment** (normal behavior). `scripts/holdout-reconcile.sh` joins the arm assignments with measured per-session Claude token usage from transcripts and reports against the pre-registered rule: **≥25% mean savings net of subagent overhead, ≤1 additional task failure, over ≥20 sessions.**

- PASS → invest in strict mode and contract enforcement.
- FAIL → reposition DevSquad honestly as a capacity/budget manager.

## Architecture

See **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** for the end-to-end flow diagram, the wrapper contract, the add-a-CLI recipe, and deployment modes. In brief:

```
Claude session
  ├─ hooks/             SessionStart · PreToolUse · PreCompact · Stop
  ├─ agents/            thin Claude shells (one per CLI × role)
  ├─ lib/*-wrapper.sh   per-CLI configuration (thin)
  └─ lib/adapter.sh     shared invocation core — cooldown, model/tier
                        resolution, bounded exec, error classification,
                        telemetry, contract logging (one path, all CLIs)
```

Supporting libs: `state.sh`, `usage.sh`, `enforcement.sh`, `routing.sh` (static keyword table — see [ROUTING-CHANGELOG.md](ROUTING-CHANGELOG.md)), `cli-detect.sh`, `model-catalog.sh`.

## Configuration

Stored in `.devsquad/config.json` (per project, self-gitignored, created on first run):

```json
{
  "enforcement_mode": "advisory",
  "holdout_mode": false,
  "default_routes": {
    "research": "gemini",
    "reading": "gemini",
    "development": "gemini",
    "code_generation": "codex",
    "testing": "codex",
    "synthesis": "self"
  },
  "preferences": {
    "gemini_word_limit": 300,
    "codex_line_limit": 50,
    "grok_word_limit": 300,
    "auto_suggest": true
  },
  "agent_models": {}
}
```

`default_routes` accept `gemini | codex | grok | self`. `agent_models` maps an agent to a model name or a `tier:fast` / `tier:frontier` pin.

## Tests

```bash
bash test/run.sh
```

No network, no real CLIs (wrappers are exercised against fake binaries), bash-3 compatible, jq-less paths covered. Suite spans routing, hook behavior, model/tier resolution, and the cross-CLI wrapper contract (`test/test_wrapper_contract.sh` — the D4 conformance suite every wrapper must pass).

## Known limitations

- Routing is a static keyword table (lexical cues). An offline benchmark shows an LLM classifier would route real tasks better, but volume doesn't yet justify it — recorded as evidence in [ROUTING-CHANGELOG.md](ROUTING-CHANGELOG.md), not implemented.
- Strict mode requires `jq` and degrades to advisory without it.
- The daily-budget zone reads Claude Code's stats cache, which is a global daily signal, not per-session context — the context zone (transcript-measured) is the one that drives thresholds.
- Grok runs a full agent session per call (~2–5 min latency); use generous timeouts.
- Codex models can't be listed programmatically, so tier pins don't apply to `codex-*` agents (use `preferences.codex_model`).

## License

MIT © [Dikshant Joshi](https://github.com/joshidikshant)
