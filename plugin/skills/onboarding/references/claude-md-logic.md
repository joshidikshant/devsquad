# CLAUDE.md Snippet Logic

## Evaluation Rules

Before offering the snippet, evaluate the developer's setup:

| Condition | Recommendation |
|-----------|---------------|
| No CLAUDE.md exists | Recommend yes — baseline delegation awareness |
| Small CLAUDE.md (<50 lines) without markers | Recommend yes — complements lightweight config |
| Large CLAUDE.md (50+ lines) without markers | Recommend caution — hooks already enforce rules |
| CLAUDE.md with existing DEVSQUAD markers | Recommend update — regenerate with updated preferences |
| `claude_md_managed: true` in config (re-run) | Skip evaluation, default to "update" |

## Snippet Generation

Generate using:
```bash
SNIPPET=$(bash ${CLAUDE_PLUGIN_ROOT}/skills/onboarding/scripts/generate-claude-md.sh \
  --plugin-root "${CLAUDE_PLUGIN_ROOT}" \
  --project-dir "${CLAUDE_PROJECT_DIR}")
```

If script fails, fall back to minimal snippet with DEVSQUAD-START/END markers.

Always present the generated snippet for review before insertion. Ask for explicit confirmation.

## Insertion

```bash
bash ${CLAUDE_PLUGIN_ROOT}/skills/onboarding/scripts/generate-claude-md.sh \
  --plugin-root "${CLAUDE_PLUGIN_ROOT}" \
  --project-dir "${CLAUDE_PROJECT_DIR}" \
  --insert
```

Save `claude_md_managed: true|false` to config.json after the decision.

## Error Handling

- If generation script fails: warn and offer minimal snippet
- If CLAUDE.md is read-only: report error with permission fix suggestion
- If markers are corrupted: replace entire section between markers
