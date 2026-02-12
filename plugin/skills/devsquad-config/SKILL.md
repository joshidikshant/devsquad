---
name: devsquad-config
description: View and edit delegation preferences
version: 1.0.0
---

# DevSquad Config Skill

Displays and updates DevSquad configuration without re-running onboarding.

## When to Use

Invoke when the user wants to:
- View current configuration settings
- Update delegation preferences
- Change enforcement mode
- Adjust word/line limits for agents
- Modify default routing for task types

## Usage

### View Current Config
```bash
bash "${CLAUDE_PLUGIN_ROOT}/skills/devsquad-config/scripts/show-config.sh"
```

### Update a Setting
```bash
bash "${CLAUDE_PLUGIN_ROOT}/skills/devsquad-config/scripts/update-config.sh" "key=value"
```

## Editable Keys

| Key | Type | Valid Values | Description |
|-----|------|--------------|-------------|
| `enforcement_mode` | string | advisory, strict | Delegation enforcement mode |
| `gemini_word_limit` | number | 0-1000 | Word limit for Gemini responses (0=disabled) |
| `codex_line_limit` | number | 0-200 | Line limit for Codex responses (0=disabled) |
| `auto_suggest` | boolean | true, false | Auto-suggest delegation when applicable |
| `default_routes.research` | string | gemini, codex, self | Default agent for research tasks |
| `default_routes.reading` | string | gemini, self | Default agent for reading tasks |
| `default_routes.code_generation` | string | gemini, codex | Default agent for code generation |
| `default_routes.testing` | string | gemini, codex | Default agent for testing tasks |
| `default_routes.synthesis` | string | self | Default agent for synthesis tasks |

## Output Format

### show-config.sh
```
=== DevSquad Configuration ===

Enforcement Mode: advisory

Default Routes:
  research:        gemini
  reading:         gemini
  code_generation: codex
  testing:         codex
  synthesis:       claude

Preferences:
  gemini_word_limit: 300
  codex_line_limit:  50
  auto_suggest:      true

Edit: /devsquad:config enforcement_mode=strict
```

### update-config.sh
```
Updated enforcement_mode to strict
```

## Dependencies

- lib/state.sh - State management and atomic writes
- .devsquad/config.json - Configuration storage
- jq (required for updates) - JSON manipulation

## Error Handling

- Invalid keys: "Unknown config key: {key}"
- Invalid values: "Invalid value for {key}: {value}. Expected: {valid_values}"
- Missing jq: "jq required for config updates. Install: brew install jq"
