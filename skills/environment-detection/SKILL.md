---
name: environment-detection
description: This skill should be used when the user asks to "detect environment", "check what tools are available", "run setup", "scan for plugins", or when DevSquad needs to know which AI CLIs and plugins are installed. Provides environment scanning, CLI detection, and plugin discovery.
version: 0.1.0
---

# Environment Detection Skill

Detect the developer's AI tooling landscape to determine which CLIs are installed, which plugins are active, and what degradation strategies are needed. This skill forms the foundation of DevSquad's onboarding flow and is invoked automatically during setup or on demand when the user wants to understand their current tooling.

## Overview

DevSquad relies on external AI CLIs (Gemini, Codex) for delegation. Not every developer has all tools installed. This skill scans the environment to discover what is available, maps installed Claude Code plugins to avoid conflicts, and produces structured JSON reports that other skills and commands consume.

The detection covers three areas:

1. **CLI availability** -- Which of the three AI CLIs (gemini, codex, claude) are installed, where they are located, and what versions they report.
2. **Dependency checking** -- Whether critical utilities like jq are present for full functionality.
3. **Plugin discovery** -- Which Claude Code plugins are installed, their command namespaces, and whether they conflict with DevSquad.

The output of this skill drives the delegation engine. If Gemini is missing, research tasks cannot be delegated to it. If Codex is missing, boilerplate generation stays with Claude. The skill quantifies exactly what the developer can and cannot do.

## Detection Process

To run environment detection, execute the following scripts via the Bash tool. Both scripts output JSON to stdout and warnings to stderr.

### Step 1: Detect AI CLIs

Execute the environment detection script:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/skills/environment-detection/scripts/detect-environment.sh"
```

If `CLAUDE_PLUGIN_ROOT` is not set, the script resolves the plugin root from its own location automatically.

The script performs the following operations:

1. Sources `lib/cli-detect.sh` from the plugin root to get base detection functions.
2. Checks availability of gemini, codex, and claude using `command -v` (POSIX portable, no reliance on `which`).
3. Records the full path to each CLI binary.
4. Attempts version detection with a 5-second timeout per CLI. Uses `gtimeout` on macOS if available, falls back to `timeout` on Linux, and skips version detection gracefully if neither exists.
5. Checks for jq availability as a dependency.
6. Computes a summary: which agents are available, which are missing, whether delegation is possible, and whether the environment is degraded.

The output JSON has this structure:

```json
{
  "timestamp": "2026-02-11T10:00:00Z",
  "clis": {
    "gemini": {"available": true, "path": "/path/to/gemini", "version": "1.2.3"},
    "codex": {"available": false, "path": "", "version": ""},
    "claude": {"available": true, "path": "/path/to/claude", "version": "1.0.0"}
  },
  "dependencies": {
    "jq": {"available": true}
  },
  "summary": {
    "available_agents": ["gemini", "claude"],
    "missing_agents": ["codex"],
    "can_delegate": true,
    "degraded": true
  }
}
```

### Step 2: Discover Installed Plugins

Execute the plugin discovery script:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/skills/environment-detection/scripts/detect-plugins.sh"
```

The script performs the following operations:

1. Checks for the GSD (Get Shit Done) plugin by looking for the `~/.claude/get-shit-done/` directory.
2. If `~/.claude/plugins/installed_plugins.json` exists and jq is available, extracts all registered plugin names from the registry.
3. Scans `~/.claude/plugins/cache/` for known plugin directories (superpowers, tribe, claude-engineer, cline).
4. Deduplicates all discovered plugins.
5. Maps each discovered plugin to its known command namespace and state directory, flagging potential conflicts.

The output JSON has this structure:

```json
{
  "plugins": [
    {"name": "superpowers", "source": "marketplace", "active": true},
    {"name": "gsd", "source": "custom", "active": true}
  ],
  "interaction_points": {
    "gsd": {"commands": "/gsd:*", "state_dir": ".planning/", "coexistence": "safe"},
    "superpowers": {"commands": "/brainstorm, /execute-plan, /write-plan", "coexistence": "safe"}
  }
}
```

### Step 3: Build Combined Report

After running both scripts, parse their JSON outputs and combine them into a single environment report. The combined report should include all CLI information, all plugin information, and a unified assessment of what the developer can do.

Store the combined report in the DevSquad state directory at `.devsquad/environment.json` for consumption by other skills and the setup command.

## Interpreting Results

### Delegation Capability

The `summary.can_delegate` field indicates whether at least one external agent (gemini or codex) is available for work delegation. When `can_delegate` is true, DevSquad can offload tasks to external CLIs. When false, all work stays with Claude.

The `summary.degraded` field indicates whether any CLI is missing. A degraded environment still works but with reduced capability. DevSquad adjusts its behavior based on what is available.

### Graceful Degradation Rules

When specific tools are missing, apply these degradation strategies:

**No Gemini installed:**
- Research and reading tasks that would normally be delegated to Gemini stay with Claude. This increases Claude's token consumption since Gemini has a 1M context window versus Claude's 200K.
- Codebase exploration tasks can be redirected to subagents (using Haiku model) if available, which provides partial mitigation.
- Web search capability is lost entirely since DevSquad routes all web searches through Gemini.
- Suggest the user install Gemini CLI: `npm install -g @anthropic/gemini-cli` or check Gemini documentation for installation instructions.

**No Codex installed:**
- Code generation and boilerplate tasks stay with Claude instead of being delegated to Codex.
- Draft generation for repetitive code patterns becomes more expensive in terms of Claude's context window.
- Suggest the user install Codex CLI: `npm install -g @openai/codex` or check Codex documentation for installation instructions.

**No jq installed:**
- State management operations that rely on jq for JSON parsing will use fallback mechanisms. The lib/state.sh library is designed to degrade gracefully without jq, but some advanced JSON operations may be limited.
- Plugin discovery from the registry file will return minimal results since jq is needed to parse installed_plugins.json.
- Warn the user to install jq for full functionality: `brew install jq` on macOS or `apt-get install jq` on Linux.

**All CLIs missing (except Claude):**
- DevSquad operates in solo mode. No delegation occurs. Claude handles all tasks directly.
- The plugin still functions but cannot optimize token usage through delegation.
- Strongly recommend installing at least Gemini for research delegation, as this provides the highest token savings.

### Reading the Summary

The `available_agents` array lists all CLIs that were found and are ready to use. The `missing_agents` array lists CLIs that were not found. Together they always cover all three CLIs: gemini, codex, claude.

Check the `path` field for each CLI to verify it resolves to the expected binary. Sometimes multiple versions may be installed, and the path reveals which one will be used.

The `version` field may be empty if version detection timed out or the CLI does not support `--version`. An empty version does not mean the CLI is broken -- it just means version information could not be retrieved.

## Plugin Coexistence Rules

DevSquad is designed to coexist peacefully with all known Claude Code plugins. The following rules govern how DevSquad avoids conflicts:

### Namespace Ownership

DevSquad owns the following namespaces exclusively:
- **Command namespace:** `/devsquad:*` -- All DevSquad slash commands are prefixed with `/devsquad:`.
- **State directory:** `.devsquad/` -- All DevSquad runtime state is stored here, never in other plugin directories.
- **Hook namespace:** DevSquad hooks are registered under the `devsquad` prefix in hooks.json.

### Boundaries with Other Plugins

**GSD (Get Shit Done):**
- GSD uses `/gsd:*` commands and `.planning/` state directory.
- DevSquad never writes to `.planning/` or registers `/gsd:*` commands.
- Coexistence is safe. Both plugins can be active simultaneously.

**Superpowers:**
- Superpowers uses `/brainstorm`, `/execute-plan`, `/write-plan` and similar commands.
- DevSquad never registers commands that overlap with Superpowers.
- Coexistence is safe.

**Tribe:**
- Tribe uses `/tribe:*` commands and `.tribe/` state directory.
- DevSquad never writes to `.tribe/` or registers `/tribe:*` commands.
- Coexistence is safe.

**Unknown plugins:**
- The plugin discovery script marks unknown plugins with `coexistence: "unknown"`.
- DevSquad does not interact with unknown plugins in any way.
- If a conflict is detected in the future, DevSquad will warn the user and suggest resolution steps.

### Conflict Detection

The `interaction_points` section of the plugin discovery output maps each plugin to its known command namespace and state directory. To check for conflicts:

1. Verify that no other plugin uses `/devsquad:*` commands.
2. Verify that no other plugin writes to `.devsquad/` directory.
3. If conflicts are found, report them to the user with specific details about which plugin conflicts and how.

In practice, conflicts are extremely rare because Claude Code plugins use distinct namespaces by convention.

## Reference Files

- `scripts/detect-environment.sh` -- Full CLI detection script with version info, dependency checking, and delegation summary. Sources `lib/cli-detect.sh` for base detection functions.
- `scripts/detect-plugins.sh` -- Plugin discovery script that scans the registry file, cache directories, and known plugin locations. Outputs plugin list with interaction points and coexistence status.
- `../../lib/cli-detect.sh` -- Base CLI detection library providing `detect_cli`, `detect_cli_path`, `detect_all_clis`, and `check_jq` functions. Created in Plan 01-01.
- `../../lib/state.sh` -- State management library for reading and writing JSON state files. Used to persist detection results.

## Usage Examples

### Quick Environment Check

```bash
# Run from plugin root
bash skills/environment-detection/scripts/detect-environment.sh
```

Output tells you immediately which CLIs are available and whether delegation is possible.

### Full Plugin Scan

```bash
# Run from plugin root
bash skills/environment-detection/scripts/detect-plugins.sh
```

Output lists all installed plugins and their interaction points for conflict checking.

### Programmatic Consumption

Other DevSquad scripts can source the detection results:

```bash
# Run detection and capture output
ENV_REPORT=$(bash "${CLAUDE_PLUGIN_ROOT}/skills/environment-detection/scripts/detect-environment.sh")

# Extract specific values with jq (if available)
CAN_DELEGATE=$(echo "$ENV_REPORT" | jq -r '.summary.can_delegate')
GEMINI_AVAILABLE=$(echo "$ENV_REPORT" | jq -r '.clis.gemini.available')
```

### Integration with Setup Command

The setup command (Plan 01-03) consumes the detection output to configure DevSquad appropriately:

1. Run both detection scripts.
2. Parse results to determine delegation capabilities.
3. Generate a tailored CLAUDE.md with routing rules based on available CLIs.
4. Store the detection results in `.devsquad/environment.json` for runtime use.
