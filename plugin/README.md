# DevSquad Plugin

Engineering Manager that coordinates AI coding agents through enforced delegation.

## Overview

DevSquad transforms Claude into an Engineering Manager that preserves your 200K context window by enforcing delegation of heavy tasks to specialized agents:
- **Gemini CLI** (1M context) - Research, bulk reading, codebase analysis
- **Codex CLI** - Fast boilerplate and test generation

## Features

- ✅ **Smart Delegation**: Automatic routing based on task type
- ✅ **Token Budget Tracking**: Real-time monitoring with GREEN/YELLOW/RED zones
- ✅ **Compliance Metrics**: Track delegation effectiveness
- ✅ **Enforcement Modes**: Advisory (suggest) or Strict (block)
- ✅ **Environment Detection**: Auto-discovers available agents
- ✅ **Zero Config**: Works out of the box with sensible defaults

## Installation

### Option 1: Claude Code Plugin Directory
```bash
# Copy to your plugins directory
cp -r plugin ~/.claude/plugins/devsquad

# Restart Claude Code
```

### Option 2: Project-Specific
```bash
# In your project directory
mkdir -p .claude-plugin
cp -r plugin/.claude-plugin/* .claude-plugin/
```

## Prerequisites

**Required:**
- Claude Code CLI (latest version)
- `jq` - JSON processor (`brew install jq` on macOS)

**Optional (for full functionality):**
- Gemini CLI - For 1M context delegation
- Codex CLI - For fast code generation

## Quick Start

### 1. First Time Setup
```bash
/devsquad:setup
```

This will:
- Detect your environment (Gemini, Codex availability)
- Configure delegation preferences
- Set enforcement mode (advisory/strict)
- Generate CLAUDE.md integration

### 2. Check Status
```bash
/devsquad:status
```

Shows:
- Token usage and budget zones
- Delegation compliance rate
- Agent availability
- Today's usage statistics

### 3. Configure Preferences
```bash
# View current config
/devsquad:config

# Update specific settings
/devsquad:config enforcement_mode=strict
/devsquad:config default_routes.research=gemini
```

## Commands

| Command | Description |
|---------|-------------|
| `/devsquad:setup` | Initial onboarding and configuration |
| `/devsquad:status` | View squad health and token usage |
| `/devsquad:config` | View or edit delegation preferences |
| `/devsquad:capacity` | Report CLI usage percentages for capacity-aware delegation |
| `/devsquad:generate` | Generate a new DevSquad skill from a natural language description |
| `/devsquad:git-health` | Scan repo for health issues (broken symlinks, orphaned branches, uncommitted changes) |
| `/devsquad:workflow` | Run a multi-step workflow from a JSON definition file |

## Agents

DevSquad provides specialized agents for different tasks:

### Gemini Agents (1M context)
- `@gemini-reader` - Bulk file analysis
- `@gemini-researcher` - Research tasks
- `@gemini-developer` - Code generation with full codebase context
- `@gemini-tester` - Test generation with pattern analysis

### Codex Agents
- `@codex-developer` - Fast boilerplate drafts
- `@codex-tester` - Quick test scaffolding

## Configuration

Configuration is stored in `.devsquad/config.json`:

```json
{
  "enforcement_mode": "advisory",
  "preferences": {
    "gemini_word_limit": 400,
    "codex_line_limit": 200,
    "auto_suggest": true
  },
  "default_routes": {
    "research": "gemini",
    "reading": "gemini",
    "code_generation": "codex",
    "testing": "codex",
    "synthesis": "self"
  }
}
```

### Enforcement Modes

**Advisory Mode** (default):
- Suggests delegation when thresholds exceeded
- User can override and proceed

**Strict Mode**:
- Blocks operations that exceed thresholds
- Forces delegation to preserve context

## Budget Zones

DevSquad tracks token usage in three zones:

- 🟢 **GREEN** (< 100K tokens): Light usage, all features available
- 🟡 **YELLOW** (100K-200K tokens): Moderate usage, delegation encouraged
- 🔴 **RED** (≥ 200K tokens): Heavy usage, strict enforcement recommended

## How It Works

### Delegation Enforcement

DevSquad uses hooks to intercept tool calls:

1. **PreToolUse Hook**: Checks Read/WebSearch operations against thresholds
2. **Routing Logic**: Analyzes task type and suggests optimal agent
3. **State Tracking**: Records usage and compliance metrics
4. **Zone-Based Adjustment**: Tightens enforcement as budget increases

### Automatic Routing

Tasks are routed based on characteristics:

| Task Type | Default Agent | Why |
|-----------|---------------|-----|
| Research | Gemini | Needs web search + large context |
| Bulk Reading | Gemini | 1M context handles entire codebases |
| Code Generation | Codex | Fast, focused boilerplate |
| Testing | Codex | Pattern-based test scaffolding |
| Synthesis | Claude | Strategic decisions need main context |

## Troubleshooting

### Hooks Not Firing
```bash
# Check hook configuration
cat .devsquad/config.json

# Verify hook scripts are executable
ls -la hooks/scripts/
```

### Missing Dependencies
```bash
# Check for jq
which jq

# Check for Gemini/Codex
which gemini
which codex
```

### Reset Configuration
```bash
# Remove config and re-run setup
rm -rf .devsquad/
/devsquad:setup
```

## Development

### Project Structure
```
plugin/
├── .claude-plugin/
│   └── plugin.json          # Plugin manifest
├── commands/                 # Slash commands
│   ├── setup.md
│   ├── config.md
│   └── status.md
├── agents/                   # Specialized agents
│   ├── gemini-reader.md
│   ├── gemini-researcher.md
│   ├── gemini-developer.md
│   ├── gemini-tester.md
│   ├── codex-developer.md
│   └── codex-tester.md
├── skills/                   # Task logic
│   ├── environment-detection/
│   ├── devsquad-config/
│   ├── devsquad-status/
│   ├── devsquad-dispatch/
│   └── onboarding/
├── hooks/                    # Event handlers
│   ├── hooks.json
│   └── scripts/
│       ├── session-start.sh
│       ├── pre-tool-use.sh
│       ├── pre-compact.sh
│       └── stop.sh
└── lib/                      # Shared libraries
    ├── state.sh
    ├── usage.sh
    ├── enforcement.sh
    ├── routing.sh
    ├── gemini-wrapper.sh
    ├── codex-wrapper.sh
    └── cli-detect.sh
```

## License

MIT License - see LICENSE file for details

## Author

Dikshant Joshi

## Links

- [GitHub Repository](https://github.com/dikshantjoshi/devsquad)
- [Report Issues](https://github.com/dikshantjoshi/devsquad/issues)

## Version History

### v0.2.0 (Current)
- Model config and plugin structure fixes
- Code simplification across hooks and lib scripts
- Additional commands: capacity, generate, git-health, workflow
- Workflow orchestration skill with permission gates and auto-commits

### v0.1.0
- Initial release
- Bug fixes:
  - Fixed integer division in compliance calculation
  - Added input token tracking for agents
  - Added budget percentage display
  - Schema-driven config validation
- Core features:
  - Delegation enforcement with hooks
  - 6 specialized agents (Gemini + Codex)
  - Token budget tracking with zones
  - Compliance metrics
  - Environment detection
