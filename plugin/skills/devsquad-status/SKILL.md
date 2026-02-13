---
name: devsquad-status
description: Shows squad health, token usage, and delegation compliance stats
version: 1.0.0
---

# DevSquad Status Skill

Displays current squad health metrics including token usage, budget zone, delegation compliance, and agent availability.

## When to Use

Invoke when the user wants to check:
- Current token budget and zone status (green/yellow/red)
- Live usage percentages from Claude, Gemini, and Codex CLIs
- Delegation compliance statistics
- Squad availability (which agents are installed)
- Capacity-aware delegation recommendations

## Capacity-Aware Delegation

Status reads from a local capacity cache (`.devsquad/capacity.json`) populated by `/devsquad:capacity`.

Users report their current CLI usage percentages by checking:
- **Claude**: `/status` → "Current session" percentage
- **Gemini**: `/stats` → "Usage left" (subtract from 100)
- **Codex**: `/status` → "5h limit" and "Weekly limit" (subtract from 100)

The cache expires after **30 minutes**, after which status shows a "STALE" warning and recommends re-running `/devsquad:capacity`.

### Zone Thresholds
- 🟢 Green: < 50% usage — normal operation
- 🟡 Yellow: 50-75/80% — enforce delegation
- 🔴 Red: > 75/80% — critical, delegate everything

## Usage

```bash
bash "${CLAUDE_PLUGIN_ROOT}/skills/devsquad-status/scripts/show-status.sh"
```

For machine-readable output:
```bash
bash "${CLAUDE_PLUGIN_ROOT}/skills/devsquad-status/scripts/show-status.sh" --json
```

## Output Format

### Human-Readable (default)
```
=== DevSquad Status ===

Token Budget:
  Zone: GREEN (42% used)
  Input tokens:  84,000
  Output tokens: 12,000
  Total: 96,000 / 200,000

Squad Usage (this session):
  Gemini: 5 invocations, ~12,400 chars response
  Codex:  2 invocations, ~3,200 chars response
  Self:   8 calls

Delegation Compliance:
  Suggestions made: 3
  Overrides (advisory): 1
  Compliance rate: 67%

Squad Availability:
  Gemini CLI: available
  Codex CLI:  available
```

### JSON Format (with --json)
Returns the raw JSON output from `get_usage_summary` function.

## Dependencies

- lib/state.sh - State management
- lib/usage.sh - Usage tracking and zone calculation
- .devsquad/logs/delegation.log - Delegation suggestions log
- .devsquad/logs/compliance.log - Compliance override log
