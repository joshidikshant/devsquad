# DevSquad Plugin Audit Report

**Date:** 2026-02-13
**Plugin:** devsquad@devsquad-marketplace v0.1.0
**Location:** `production/plugin/`
**Auditor:** Claude + Gemini + plugin-dev guidelines

---

## Executive Summary

| Severity | Count | Status |
|----------|-------|--------|
| CRITICAL | 1 | FIXED — onboarding skill refactored |
| WARNING | 9 | 2 FIXED (codex agent examples), 7 remaining (bash practices) |
| INFO | 12+ | Quality notes, best practices |

**Overall:** Plugin loads and hooks fire correctly. Core structure is sound. Critical issues resolved. Remaining warnings are hardening items for v1.0.

**Note:** `lib/gemini-wrapper.sh` sourcing was initially flagged as critical but verified working correctly — `lib_dir` resolves via `BASH_SOURCE` to the correct directory. False positive from out-of-context execution.

---

## 1. Plugin Structure

### Manifest (plugin.json) - PASS

```json
{
  "name": "devsquad",
  "version": "0.1.0",
  "description": "Engineering Manager that coordinates AI coding agents through enforced delegation",
  "author": { "name": "Dikshant Joshi" },
  "license": "MIT",
  "keywords": ["delegation", "orchestration", "gemini", "codex", "multi-agent", "token-management"]
}
```

- Name: kebab-case, unique
- Version: semver
- Description: clear
- Missing: `repository` and `homepage` fields (INFO)

### Marketplace Structure - PASS

```
production/
├── .claude-plugin/marketplace.json  # Marketplace manifest
├── plugin/                          # Plugin root
│   ├── .claude-plugin/plugin.json   # Plugin manifest
│   ├── agents/    (6 files)
│   ├── commands/  (3 files)
│   ├── hooks/     (hooks.json + 4 scripts)
│   ├── lib/       (7 files)
│   └── skills/    (5 directories)
├── install.sh
├── README.md
├── CHANGELOG.md
└── LICENSE
```

- marketplace.json and plugin.json properly separated
- No circular symlinks
- Auto-discovery directories at correct level

---

## 2. Hooks (hooks.json) - PASS

Schema is correct after fix. All 4 events use proper inner `hooks` array:

| Event | Matcher | Script | Timeout |
|-------|---------|--------|---------|
| SessionStart | — | session-start.sh | 15s |
| PreCompact | — | pre-compact.sh | 15s |
| PreToolUse | `Read\|WebSearch` | pre-tool-use.sh | 15s |
| Stop | — | stop.sh | 15s |

All scripts pass `bash -n` syntax check.

---

## 3. Commands - PASS

| Command | Frontmatter | allowed-tools | Delegates to skill |
|---------|-------------|---------------|-------------------|
| setup.md | description, argument-hint, allowed-tools | Read,Write,Edit,Bash,Glob,Grep,Skill | devsquad:onboarding |
| config.md | description, argument-hint, allowed-tools | Read,Write,Bash,Skill | devsquad:config |
| status.md | description, argument-hint, allowed-tools | Read,Bash,Skill | devsquad:status |

All commands properly delegate to skills. Format matches plugin-dev guidelines.

---

## 4. Agents

### Passing (4/6)

| Agent | Frontmatter | Examples | System Prompt |
|-------|-------------|----------|---------------|
| gemini-developer.md | Complete | `<example>` tags | Clear, focused |
| gemini-reader.md | Complete | `<example>` tags | Enforces Read prohibition |
| gemini-researcher.md | Complete | `<example>` tags | Bans WebSearch |
| gemini-tester.md | Complete | `<example>` tags | Pattern matching focus |

### WARNING (2/6)

| Agent | Issue | Impact |
|-------|-------|--------|
| **codex-developer.md** | Missing `<example>` trigger tags | Reduced auto-activation reliability |
| **codex-tester.md** | Missing `<example>` trigger tags | Reduced auto-activation reliability |

**Fix:** Add `<example>` blocks matching the Gemini agent format.

---

## 5. Skills

### CRITICAL: onboarding/SKILL.md

- **Word count:** ~1,460 words
- **Guideline:** Skills should be lean (< 500 words for body)
- **Impact:** Bloats context window every time onboarding activates
- **Fix:** Refactor to progressive disclosure — move detailed steps to `references/` files

### WARNING: environment-detection/SKILL.md

- **Word count:** ~750 words
- **Imperative form:** No
- **Fix:** Compress to < 400 words, convert to imperative form

### Passing (3/5)

| Skill | Words | Frontmatter | ${CLAUDE_PLUGIN_ROOT} | Imperative |
|-------|-------|-------------|----------------------|------------|
| devsquad-config | ~180 | OK | Yes | Yes |
| devsquad-dispatch | ~150 | OK | Yes | Yes |
| devsquad-status | ~110 | OK | Yes | Yes |

---

## 6. Lib Files

### gemini-wrapper.sh - PASS (false positive corrected)

Initially flagged as broken sourcing. Verified that `lib_dir` resolves correctly via `dirname "${BASH_SOURCE[0]}"` which points to the `lib/` directory itself. All sourced files (`state.sh`, `usage.sh`) and their functions (`check_rate_limit`, `update_agent_stats`, `record_usage`) load correctly when invoked from plugin context. The runtime errors in the initial audit came from execution outside the proper plugin directory context.

### WARNING: All 7 lib files

| File | Issue |
|------|-------|
| cli-detect.sh | Missing `set -euo pipefail` |
| codex-wrapper.sh | Missing `set -euo pipefail` |
| enforcement.sh | Missing `set -euo pipefail` |
| gemini-wrapper.sh | Broken sourcing + missing strict mode |
| routing.sh | Missing `set -euo pipefail` |
| state.sh | Missing strict mode (but has excellent atomic writes) |
| usage.sh | Hardcoded `~/.claude/stats-cache.json` |

### WARNING: Inconsistent path resolution

Some files use `${CLAUDE_PLUGIN_ROOT}`, others use `dirname "${BASH_SOURCE[0]}"`, others use `CLAUDE_PROJECT_DIR`. Should standardize.

---

## 7. Hook Scripts

| Script | ${CLAUDE_PLUGIN_ROOT} | Lib sourcing | Error handling | Issues |
|--------|----------------------|-------------|----------------|--------|
| session-start.sh | Partial | OK | Robust | Hardcoded `~/.claude/plugins/installed_plugins.json` |
| pre-tool-use.sh | Partial | OK | OK | Hardcoded `~/.devsquad` state path |
| pre-compact.sh | BASH_SOURCE | OK | OK | Recursion guard OK |
| stop.sh | OK | OK | OK | Loop guard OK |

---

## 8. Priority Action Items

### P0 — Critical (fix before v1.0)

1. ~~**Fix `lib/gemini-wrapper.sh` sourcing**~~ — FALSE POSITIVE, verified working correctly
2. ~~**Refactor `skills/onboarding/SKILL.md`**~~ — FIXED: refactored from 1,460 to ~350 words, extracted to 4 reference files

### P1 — High (fix for quality)

3. ~~**Add `<example>` tags to codex-developer.md and codex-tester.md**~~ — FIXED: 3 examples each
4. **Add `set -euo pipefail` to all lib/*.sh files** — prevents silent failures

### P2 — Medium (improve robustness)

5. **Compress `skills/environment-detection/SKILL.md`** — 750 words to < 400
6. **Standardize `${CLAUDE_PLUGIN_ROOT}` usage** across all hook scripts and lib files
7. **Add `repository` and `homepage` to plugin.json**

### P3 — Low (nice to have)

8. Abstract hardcoded paths (`~/.devsquad`, `~/.claude/stats-cache.json`) into configurable constants
9. Add a README.md inside `plugin/` directory (separate from repo root README)

---

## 9. Validation Checks Passed

- [x] Plugin loads successfully (`claude plugin list` shows enabled)
- [x] Slash commands appear (`/devsquad:setup`, `/devsquad:config`, `/devsquad:status`)
- [x] hooks.json schema matches working plugins (hookify, ralph-loop)
- [x] All .sh files pass `bash -n` syntax check
- [x] All .json files pass `jq .` validation
- [x] Source and cached plugin are in sync
- [x] No circular symlinks
- [x] marketplace.json and plugin.json properly separated
- [x] Marketplace add + install flow works end-to-end
- [x] install.sh one-command installer created

---

## 10. Comparison with Reference Plugins

| Feature | DevSquad | hookify | ralph-loop | security-guidance |
|---------|----------|---------|------------|-------------------|
| hooks.json schema | PASS (fixed) | PASS | PASS | PASS |
| ${CLAUDE_PLUGIN_ROOT} | Partial | Full | Full | Full |
| `<example>` on agents | 4/6 | N/A | N/A | N/A |
| Skill word count | 2 over limit | Under limit | Under limit | Under limit |
| Bash strict mode | Missing | Present | Present | N/A |
