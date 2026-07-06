# Configuration Schema

## config.json Structure

```json
{
  "version": 1,
  "created": "ISO-8601 timestamp",
  "updated": "ISO-8601 timestamp",
  "enforcement_mode": "advisory|strict",
  "default_routes": {
    "research": "gemini|codex|claude",
    "reading": "gemini|codex|claude",
    "development": "gemini|codex|claude",
    "code_generation": "codex|gemini|claude",
    "testing": "codex|gemini|claude",
    "synthesis": "claude"
  },
  "preferences": {
    "gemini_word_limit": 300,
    "codex_line_limit": 50,
    "auto_suggest": true
  },
  "environment": {
    "gemini_available": true,
    "codex_available": true,
    "claude_available": true,
    "detected_plugins": ["superpowers", "gsd"]
  }
}
```

## Re-run Merge Rules

When `/devsquad:setup` is invoked and `.devsquad/config.json` already exists:

1. Load the existing configuration as the starting point
2. Show "Current value" before each question so the developer knows what is already set
3. Accept empty responses to keep the current value unchanged
4. Only overwrite fields the developer explicitly provides new values for
5. Update the `updated` timestamp but preserve the original `created` timestamp
6. Re-run environment detection to pick up any newly installed tools
7. Offer to regenerate the CLAUDE.md snippet with updated information

## Strict Mode Dependency Checks

Before saving strict mode, check and warn (do not block):

- `jq`: Required for full enforcement. Without it, hooks fall back to advisory.
- `gemini`: Required for Gemini delegation. Without it, falls back to advisory for Gemini routes.
- `codex`: Required for Codex delegation. Without it, falls back to advisory for Codex routes.

## Model Routing

Two layers, resolved by the wrappers as: per-agent > global > CLI default.

```json
{
  "preferences": {
    "gemini_model": "Gemini 3.1 Pro (High)",
    "codex_model": "gpt-5.3-codex"
  },
  "agent_models": {
    "gemini-reader": "Gemini 3.5 Flash (Low)",
    "gemini-researcher": "Gemini 3.1 Pro (High)",
    "codex-developer": "gpt-5.3-codex"
  }
}
```

- `agent_models.<agent-name>` — per-agent override. The agent exports
  `DEVSQUAD_AGENT=<name>` before sourcing the wrapper (see agent docs), and the
  wrapper passes the resolved model as `agy --model` / `codex exec -m`.
- Antigravity multiplexes Gemini, Claude, and GPT-OSS models — list them with
  `agy models`. `/devsquad:config agent_models.<agent>=<model>` validates the
  name against that list, because **agy silently ignores unknown model names**
  (no error, silent fallback to the session default).
- Set `DEVSQUAD_SKIP_MODEL_VALIDATION=1` to bypass validation (offline use).
- Grok: `preferences.grok_model` / `agent_models.grok-*` (list models with
  `grok models` after `grok login`; the grok CLI reports bad model names
  itself, so only non-empty is enforced). `default_routes` also accepts
  `grok` for research/development/code_generation/testing.
- **Tier pins (churn-proof, recommended)**: any model value may be
  `tier:fast` or `tier:frontier` instead of an exact name. Tiers resolve at
  invocation time against the machine-local catalog (`~/.devsquad/models.json`,
  auto-refreshed in the background when >24h old; `/devsquad:models` to
  inspect). When providers rotate models, tier pins follow automatically;
  exact-name pins go stale and trigger drift warnings at session start.
- The Claude shell each agent runs in is separate from this: it is set by
  `model:` in the agent's frontmatter (currently `sonnet` for all six).
