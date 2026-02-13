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
