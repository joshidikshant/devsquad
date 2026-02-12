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

```bash
# Clone the repo
git clone https://github.com/dikshantjoshi/devsquad.git

# Symlink to Claude Code plugins directory
mkdir -p ~/.claude/plugins
ln -s "$(pwd)/devsquad" ~/.claude/plugins/devsquad

# Run setup (inside any Claude Code session)
/devsquad:setup
```

## Usage

### Slash Commands

| Command | Description |
|---------|-------------|
| `/devsquad:setup` | Run onboarding — detect environment, set preferences, generate config |
| `/devsquad:config` | View or edit delegation preferences (e.g., `enforcement_mode=strict`) |
| `/devsquad:status` | Show squad health, token usage, delegation stats, and budget zone |

### How It Works

1. **Session starts** → `session-start` hook detects available CLIs, initializes state
2. **You work normally** → Claude handles your requests as usual
3. **Hook intercepts** → When Claude tries to Read files or WebSearch, the `pre-tool-use` hook fires
4. **Routing decides** → Task is classified and routed to the best agent (Gemini for research/reading, Codex for scaffolding/testing, Claude for synthesis)
5. **Agent executes** → Wrapper invokes the external CLI with timeout handling, rate-limit backoff, and error classification
6. **Usage tracked** → Every invocation is logged; budget zones (green/yellow/red) guide behavior
7. **Session ends** → `stop` hook persists session stats

### Enforcement Modes

| Mode | Behavior |
|------|----------|
| `advisory` | Suggests delegation, Claude can proceed anyway |
| `strict` | Blocks tool use and requires delegation (with availability-safe fallback) |

## Architecture

```
devsquad/
├── .claude-plugin/
│   └── plugin.json          # Plugin manifest
├── agents/                   # Agent personas
│   ├── codex-developer.md    # Codex for code generation
│   ├── codex-tester.md       # Codex for test generation
│   ├── gemini-developer.md   # Gemini for development tasks
│   ├── gemini-reader.md      # Gemini for file reading
│   ├── gemini-researcher.md  # Gemini for web research
│   └── gemini-tester.md      # Gemini for test generation
├── commands/                 # Slash commands
│   ├── config.md             # /devsquad:config
│   ├── setup.md              # /devsquad:setup
│   └── status.md             # /devsquad:status
├── hooks/                    # Runtime enforcement
│   ├── hooks.json            # Hook registration
│   └── scripts/
│       ├── pre-compact.sh    # Pre-compaction state save
│       ├── pre-tool-use.sh   # Delegation enforcement
│       ├── session-start.sh  # Environment detection
│       └── stop.sh           # Session cleanup
├── lib/                      # Shared libraries
│   ├── cli-detect.sh         # CLI availability detection
│   ├── codex-wrapper.sh      # Codex CLI wrapper
│   ├── enforcement.sh        # Enforcement logic
│   ├── gemini-wrapper.sh     # Gemini CLI wrapper
│   ├── routing.sh            # Task routing engine
│   ├── state.sh              # State management
│   └── usage.sh              # Usage tracking
└── skills/                   # Interactive skills
    ├── devsquad-config/      # Configuration management
    ├── devsquad-dispatch/    # Manual dispatch
    ├── devsquad-status/      # Status dashboard
    ├── environment-detection/# CLI detection
    └── onboarding/           # First-run setup
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

## License

MIT © [Dikshant Joshi](https://github.com/dikshantjoshi)
