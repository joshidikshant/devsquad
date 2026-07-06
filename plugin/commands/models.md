---
name: models
description: Show the live model catalog, tier resolutions, and model drift
---

Present the DevSquad model catalog concisely. Run these with the Bash tool:

1. Refresh the catalog (network call, a few seconds):
   `bash ${CLAUDE_PLUGIN_ROOT}/lib/model-catalog.sh refresh`

2. Show what the tiers currently resolve to:
   - `bash ${CLAUDE_PLUGIN_ROOT}/lib/model-catalog.sh resolve gemini fast`
   - `bash ${CLAUDE_PLUGIN_ROOT}/lib/model-catalog.sh resolve gemini frontier`
   - `bash ${CLAUDE_PLUGIN_ROOT}/lib/model-catalog.sh resolve grok fast`
   - `bash ${CLAUDE_PLUGIN_ROOT}/lib/model-catalog.sh resolve grok frontier`

3. Show the project's pins from `.devsquad/config.json` (`agent_models` and
   `preferences.*_model`) and, for each `tier:` pin, the model it resolves
   to right now.

4. If `~/.devsquad/models-changelog.log` has entries from the last 14 days,
   summarize the drift (models added/removed per CLI).

Explain to the user: `tier:fast` / `tier:frontier` pins follow the catalog
automatically as providers rotate models; exact-name pins are validated at
set time but can go stale — drift warnings appear at session start.
