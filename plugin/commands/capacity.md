---
name: capacity
description: Report current CLI usage percentages for capacity-aware delegation
---

# DevSquad Capacity Report

Report your current CLI usage so DevSquad can make intelligent delegation decisions.

## Instructions

Use AskUserQuestion to collect the user's current usage percentages. Explain how to check each one:

Ask the user these questions using AskUserQuestion:

**Question 1: Claude usage %**
- Header: "Claude %"
- Tell the user: "Check Claude: run `/status` then look at the 'Current session' percentage"
- Options: "0-25%", "25-50%", "50-75%", "75-100%"

**Question 2: Gemini usage %**
- Header: "Gemini %"
- Tell the user: "Check Gemini: run `gemini` then `/stats`, look at the primary model's 'Usage left' and subtract from 100"
- Options: "0-25%", "25-50%", "50-75%", "75-100%"

**Question 3: Codex 5hr usage %**
- Header: "Codex 5hr"
- Tell the user: "Check Codex: run `codex` then `/status`, look at '5h limit' percentage left and subtract from 100"
- Options: "0-25%", "25-50%", "50-75%", "75-100%"

**Question 4: Codex weekly usage %**
- Header: "Codex weekly"
- Tell the user: "From the same Codex `/status`, look at 'Weekly limit' percentage left and subtract from 100"
- Options: "0-25%", "25-50%", "50-75%", "75-100%"

After collecting answers, map the ranges to midpoint values:
- "0-25%" → 12
- "25-50%" → 37
- "50-75%" → 62
- "75-100%" → 87

If user provides exact numbers via "Other", use those directly.

Then save the capacity data:

```bash
source "${CLAUDE_PLUGIN_ROOT}/lib/state.sh"
source "${CLAUDE_PLUGIN_ROOT}/lib/usage.sh"
init_state_dir
save_capacity <claude_pct> <gemini_pct> <codex_5hr_pct> <codex_weekly_pct>
```

After saving, run the status command to show the updated report:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/skills/devsquad-status/scripts/show-status.sh"
```

Display the result to the user with the delegation recommendation.
