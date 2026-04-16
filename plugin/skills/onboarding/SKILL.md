---
name: onboarding
description: This skill should be used when the user runs "/devsquad:setup", asks to "configure devsquad", "set up delegation", "change enforcement mode", or "update agent preferences". Guides through environment detection, preference capture, and config generation.
version: 1.0.0
---

# DevSquad Onboarding

Guide the developer through environment detection, preference capture, and config generation. On re-run, load existing config as defaults and only overwrite fields explicitly changed.

## Prerequisites

- DevSquad plugin installed
- `lib/state.sh` and `lib/cli-detect.sh` available via `${CLAUDE_PLUGIN_ROOT}`

## Flow

Execute these five steps sequentially. Each builds on the previous.

### Step 1: Environment Detection

Run detection scripts to identify available CLIs and plugins:

```bash
bash ${CLAUDE_PLUGIN_ROOT}/skills/environment-detection/scripts/detect-environment.sh
bash ${CLAUDE_PLUGIN_ROOT}/skills/environment-detection/scripts/detect-plugins.sh
```

If detection scripts unavailable, fall back to:
```bash
source ${CLAUDE_PLUGIN_ROOT}/lib/cli-detect.sh
DETECTION_RESULT=$(detect_all_clis)
```

Present summary: which CLIs are available, which are missing, which plugins detected.

### Step 2: Preference Capture

Check if `.devsquad/config.json` exists (re-run). If so, load current values as defaults.

Ask three questions interactively:
1. **Enforcement mode** — advisory (default) or strict
2. **Default routes** — which agent handles research, reading, code gen, testing, synthesis
3. **Agent preferences** — word limits, line limits, auto-suggest

See `references/preference-questions.md` for full question text and validation rules.

### Step 3: Save Configuration

Initialize state directory and write config:
```bash
source ${CLAUDE_PLUGIN_ROOT}/lib/state.sh
init_state_dir
write_state "config.json" "$CONFIG_JSON"
```

On re-run, merge new values with existing config. Preserve `created` timestamp, update `updated`.

See `references/config-schema.md` for JSON schema and merge rules.

### Step 4: CLAUDE.md Snippet (Opt-in)

Evaluate project setup and recommend whether to add a DevSquad section to CLAUDE.md. This is optional — hooks work regardless.

Present recommendation, ask for explicit consent, show preview before insertion.

See `references/claude-md-logic.md` for evaluation rules and generation commands.

### Step 5: Verify Hook Registration

Check that the 4 DevSquad hooks are registered in `~/.claude/settings.json`:

```bash
python3 -c "
import json, os
s = json.load(open(os.path.expanduser('~/.claude/settings.json')))
hooks = s.get('hooks', {})
for event in ['SessionStart', 'PreToolUse', 'PreCompact', 'Stop']:
    entries = hooks.get(event, [])
    found = any('devsquad' in str(h) for e in entries for h in e.get('hooks', []))
    print(f'{event}: {\"OK\" if found else \"MISSING\"}')
"
```

If any are MISSING, inform the user and instruct them to re-run the installer:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/joshidikshant/devsquad/main/install.sh)
```

Or add them manually by running `install.sh` from the cloned repo.

### Step 6: Confirmation

Display summary of configured settings:
- Enforcement mode
- Available agents and routes
- Config file path
- CLAUDE.md status

If any CLI is missing, remind developer how to install it. If no external CLI available at all, warn that delegation cannot function.

## Error Handling

See `references/error-handling.md` for recovery procedures. Never abort mid-flow.
