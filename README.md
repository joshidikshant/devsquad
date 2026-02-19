# DevSquad

**An Engineering Manager for your AI coding agents.**

DevSquad is a [Claude Code](https://docs.anthropic.com/en/docs/claude-code) plugin that turns Claude into an Engineering Manager, coordinating a squad of AI coding agents (Gemini, Codex) through **hook-enforced delegation** — not suggestions.

Instead of Claude doing everything itself and burning through its 200K context, DevSquad intercepts tool usage, routes work to the right agent, and tracks usage across all three tools.

```
┌─────────────────────────────────────────────────┐
│                  Claude Code                     │
│                                                  │
│   You say: "Research this codebase"              │
│                                                  │
│   ┌─────────────┐    ┌──────────────────────┐   │
│   │    Hooks     │───▶│   Routing Engine     │   │
│   │ (Intercept)  │    │                      │   │
│   └─────────────┘    │  research → gemini    │   │
│                      │  reading  → gemini    │   │
│                      │  codegen  → codex     │   │
│                      │  testing  → codex     │   │
│                      │  synthesis→ claude    │   │
│                      └──────────────────────┘   │
│                              │                   │
│              ┌───────────────┼───────────────┐   │
│              ▼               ▼               ▼   │
│   ┌──────────────┐ ┌──────────────┐ ┌────────┐  │
│   │   Gemini     │ │    Codex     │ │ Claude │  │
│   │  (1M ctx)    │ │  (200K ctx)  │ │ (self) │  │
│   │  research    │ │  scaffolding │ │ synth  │  │
│   │  reading     │ │  testing     │ │        │  │
│   └──────────────┘ └──────────────┘ └────────┘  │
│              │               │               │   │
│              └───────────────┼───────────────┘   │
│                              ▼                   │
│                    ┌──────────────────┐          │
│                    │  Usage Tracker   │          │
│                    │  Budget Zones    │          │
│                    └──────────────────┘          │
└─────────────────────────────────────────────────┘
```

## Why?

CLAUDE.md instructions are ignorable. Hooks are not.

After 25+ sessions of Claude ignoring delegation rules, burning context, and requiring manual correction, we replaced documentation-based enforcement with **runtime hooks** that physically intercept tool calls and redirect work.

## Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (CLI)
- [Gemini CLI](https://github.com/google-gemini/gemini-cli) — `npm install -g @anthropic-ai/gemini-cli` (optional, graceful degradation)
- [Codex CLI](https://github.com/openai/codex) — `npm install -g @openai/codex` (optional, graceful degradation)
- `jq` — for JSON processing (`brew install jq` on macOS)

## Installation

### Quick Install

```bash
git clone https://github.com/joshidikshant/devsquad.git
cd devsquad && bash install.sh
```

### Manual Install

```bash
# Register the marketplace
claude plugin marketplace add https://github.com/joshidikshant/devsquad.git

# Install the plugin
claude plugin install devsquad@devsquad-marketplace
```

After installing, restart Claude Code and run `/devsquad:setup` to complete onboarding.

## Usage

### Slash Commands

| Command | Description |
|---------|-------------|
| `/devsquad:setup` | Run onboarding — detect environment, set preferences, generate config |
| `/devsquad:config` | View or edit delegation preferences (e.g., `enforcement_mode=strict`) |
| `/devsquad:status` | Show squad health, token usage, delegation stats, and budget zone |
| `/devsquad:git-health` | Scan repo for broken symlinks, orphaned branches, uncommitted changes |
| `/devsquad:generate <description>` | Generate a new DevSquad skill — Gemini research → Codex draft → review → write |
| `/devsquad:workflow` | Run a multi-step workflow from a JSON definition file |

### How It Works

1. **Session starts** → `session-start` hook detects available CLIs, initializes state
2. **You work normally** → Claude handles your requests as usual
3. **Hook intercepts** → When Claude tries to Read files or WebSearch, the `pre-tool-use` hook fires
4. **Routing decides** → Task is classified and routed to the best agent (Gemini for research/reading, Codex for scaffolding/testing, Claude for synthesis)
5. **Delegation advised** → After 3+ file reads in a session, Claude is prompted to delegate remaining reads to Gemini with estimated token savings shown
6. **Agent executes** → Wrapper invokes the external CLI with timeout handling, rate-limit backoff, and error classification
7. **Usage tracked** → Every invocation is logged; budget zones (green/yellow/red) guide behavior; delegation acceptance rate tracked
8. **Session ends** → `stop` hook persists session stats

### Skills

#### Git Health Check
Scans the repository for common problems that impede workflow:

```bash
/devsquad:git-health              # full scan, human-readable output
/devsquad:git-health --json       # machine-readable: {"total_issues": N, ...}
/devsquad:git-health --check symlinks|branches|changes
```

Detects: broken symlinks, orphaned branches (merged but not deleted, stale >30 days), uncommitted changes (untracked, modified, staged, ahead of remote).

#### Code Generation
Generates a new DevSquad skill from a natural language description:

```bash
/devsquad:generate "bulk rename files matching a pattern"
```

Pipeline: Gemini scans existing skills for patterns → Codex drafts SKILL.md + implementation → `[y]es / [N]o / [e]dit` review → files written to `plugin/skills/<name>/` → `bash -n` syntax validation.

#### Workflow Orchestration
Executes multi-step workflows defined in JSON:

```bash
/devsquad:workflow                                          # interactive picker
run-workflow.sh --workflow templates/feature-workflow.json  # direct
run-workflow.sh --workflow my-workflow.json --dry-run        # preview steps
```

Built-in `feature-workflow.json` template: create branch → generate skill → validate repo health → clean up staging files. Each step supports destructive gates (confirm before running), git checkpoints (auto-commit), and post-workflow health validation.

### Enforcement Modes

| Mode | Behavior |
|------|----------|
| `advisory` | Suggests delegation, Claude can proceed anyway |
| `strict` | Blocks tool use and requires delegation (with availability-safe fallback) |

## Architecture

```
devsquad/
├── .claude-plugin/
│   └── marketplace.json      # Marketplace manifest
├── install.sh                # One-line installer
├── plugin/                   # Plugin root
│   ├── .claude-plugin/
│   │   └── plugin.json       # Plugin manifest
│   ├── agents/               # Agent personas
│   │   ├── codex-developer.md
│   │   ├── codex-tester.md
│   │   ├── gemini-developer.md
│   │   ├── gemini-reader.md
│   │   ├── gemini-researcher.md
│   │   └── gemini-tester.md
│   ├── commands/             # Slash commands
│   │   ├── config.md         # /devsquad:config
│   │   ├── setup.md          # /devsquad:setup
│   │   └── status.md         # /devsquad:status
│   ├── hooks/                # Runtime enforcement
│   │   ├── hooks.json        # Hook registration
│   │   └── scripts/
│   │       ├── pre-compact.sh
│   │       ├── pre-tool-use.sh
│   │       ├── session-start.sh
│   │       └── stop.sh
│   ├── lib/                  # Shared libraries
│   │   ├── cli-detect.sh
│   │   ├── codex-wrapper.sh
│   │   ├── enforcement.sh
│   │   ├── gemini-wrapper.sh
│   │   ├── routing.sh
│   │   ├── state.sh
│   │   └── usage.sh
│   └── skills/               # Interactive skills
│       ├── code-generation/      # /devsquad:generate — Gemini→Codex pipeline
│       ├── devsquad-config/
│       ├── devsquad-dispatch/
│       ├── devsquad-status/
│       ├── environment-detection/
│       ├── git-health/           # /devsquad:git-health — repo health scanner
│       ├── onboarding/
│       └── workflow-orchestration/  # /devsquad:workflow — JSON workflow engine
```

## Configuration

Configuration is stored in `.devsquad/config.json` (created on first run):

```json
{
  "enforcement_mode": "advisory",
  "default_routes": {
    "research": "gemini",
    "reading": "gemini",
    "code_generation": "codex",
    "testing": "codex",
    "synthesis": "self"
  },
  "preferences": {
    "gemini_word_limit": 300,
    "codex_line_limit": 50,
    "auto_suggest": true
  }
}
```

## Known Limitations

- Routing is primarily keyword-based (lexical cues like `generate|boilerplate|scaffold`)
- Strict mode requires `jq` — silently degrades to advisory without it
- Usage zones are based on daily output token volume, not context window percentage
- Codex tester routing is currently manual-only (not auto-routed)
- Delegation acceptance tracking uses heuristic correlation (same-tool = decline, different-tool = accept) — hooks cannot directly observe CLI invocations
- Workflow engine requires `envsubst` or Perl for variable substitution; falls back to bash-native if neither is available
- Cleanup workflow (auto-fix repo issues) not yet implemented — planned for v2.1

## License

MIT © [Dikshant Joshi](https://github.com/joshidikshant)
