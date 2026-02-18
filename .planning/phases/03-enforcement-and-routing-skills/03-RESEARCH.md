# Phase 3: Code Generation — Research

**Researched:** 2026-02-18
**Domain:** DevSquad skill authoring — orchestrating Gemini (codebase research) + Codex (code drafting) to generate new skills from natural language descriptions
**Confidence:** HIGH (all findings verified directly from codebase source files)

---

## Summary

Phase 3 builds a `code-generation` skill inside DevSquad. When a user describes a desired command in one or two sentences, the skill must (1) use Gemini to research existing DevSquad patterns in the codebase, (2) use Codex to draft the implementation, (3) present the draft for review, and (4) write the final files into the correct skill directory structure. The skill is itself a DevSquad skill — it lives in `plugin/skills/code-generation/` and is invoked via a thin command stub.

The existing skill anatomy is well-understood and consistent: every skill has `SKILL.md` (frontmatter + prose spec) and `scripts/` containing one or more `.sh` entry points. The wrappers `gemini-wrapper.sh` and `codex-wrapper.sh` are fully featured libraries with argument conventions, rate-limit handling, and usage tracking — they are sourced, not executed. Discovery is by convention (no registry file): the command stub in `plugin/commands/` hardcodes the skill invocation path.

**Primary recommendation:** Build `scripts/generate-skill.sh` as the single entry point. It accepts the description as `$1`, runs Gemini with `@plugin/skills/` file references to learn patterns, pipes the summary into a Codex prompt to draft both `SKILL.md` and the implementation script, streams the draft to the terminal for user review/edit, then writes files only after explicit user confirmation.

---

## Standard Stack

### Core
| Component | Version/Location | Purpose | Why Standard |
|-----------|-----------------|---------|--------------|
| `plugin/lib/gemini-wrapper.sh` | current | Sourced library: `invoke_gemini`, `invoke_gemini_with_files`, `expand_dir_refs` | All Gemini delegation in DevSquad goes through this; has rate-limit, timeout, usage tracking |
| `plugin/lib/codex-wrapper.sh` | current | Sourced library: `invoke_codex` | All Codex delegation goes through this; same error taxonomy |
| `plugin/lib/state.sh` | current | `init_state_dir`, `read_state`, `write_state`, `ensure_config`, `record_usage` | Canonical JSON state I/O |
| `plugin/lib/routing.sh` | current | `route_task()` → JSON output | Used by dispatch skill; can be referenced for category patterns |
| `jq` | system | JSON assembly for SKILL.md frontmatter and manifest files | Used by every existing script for JSON; fallback paths exist |

### Supporting
| Component | Location | Purpose | When to Use |
|-----------|---------|---------|-------------|
| `plugin/lib/usage.sh` | plugin/lib/ | `record_usage`, `update_agent_stats` | Called by wrappers automatically; only needed if code-generation script bypasses wrappers |
| `DEVSQUAD_HOOK_DEPTH=1` | env var convention | Prevents recursive hook firing when skill runs Bash sub-calls | Must be set before calling `gemini` or `codex` CLI directly (wrappers set it automatically) |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Sourcing `gemini-wrapper.sh` | Calling `gemini -p` directly | Direct call skips rate-limit tracking, usage recording, and hook-depth guard — do not use |
| Single combined Gemini+Codex prompt | Two-stage research then draft | Combined prompt loses the research step; Gemini's codebase context doesn't transfer to Codex without explicit relay |

**Installation:** No new dependencies. All tooling is already present in `plugin/lib/`.

---

## Architecture Patterns

### Recommended Project Structure
```
plugin/
├── skills/
│   └── code-generation/         # New skill directory
│       ├── SKILL.md             # Skill spec (frontmatter + prose)
│       └── scripts/
│           └── generate-skill.sh  # Main entry point
├── commands/
│   └── generate.md              # Thin command stub
```

### Pattern 1: SKILL.md Frontmatter + Prose Spec
**What:** Every skill has a YAML frontmatter block followed by markdown prose. The frontmatter provides machine-readable metadata; the prose is the system prompt / instruction set Claude reads when activating the skill.

**When to use:** This is the required format — no deviation.

**Verified format (from devsquad-config/SKILL.md and onboarding/SKILL.md):**
```markdown
---
name: skill-name
description: One-sentence activation trigger description. Used by Claude to decide when to invoke.
version: 1.0.0
---

# Skill Title

Prose description of what the skill does.

## When to Use
...

## Usage
\`\`\`bash
bash "${CLAUDE_PLUGIN_ROOT}/skills/skill-name/scripts/main-script.sh"
\`\`\`

## Dependencies
- lib/state.sh — ...
```

**Key rule:** The `description` field in frontmatter doubles as the skill activation trigger. It must read naturally as a condition, e.g. "This skill should be used when the user asks to generate a new skill or command."

### Pattern 2: Thin Command Stub
**What:** `plugin/commands/<name>.md` is a minimal markdown file with YAML frontmatter that tells Claude which tools are allowed and what arguments to accept. The body contains a one-line instruction to invoke the skill.

**Verified format (from config.md and status.md):**
```markdown
---
description: One-line description shown in command help
argument-hint: "Optional: description of accepted arguments"
allowed-tools: ["Read", "Write", "Bash", "Skill"]
---

# Command Title

Arguments: $ARGUMENTS

Invoke the devsquad:<skill-name> skill and follow it exactly.
```

**Command naming:** File name becomes the command. `generate.md` → `/devsquad:generate`.

### Pattern 3: Script Entry Point Conventions
**What:** Main scripts follow a consistent structure verified across `git-health.sh`, `show-config.sh`, `route-task.sh`.

**Verified structure:**
```bash
#!/usr/bin/env bash
set -euo pipefail

# Resolve paths (ALWAYS use this pattern)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PLUGIN_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

# Source required libraries
source "${PLUGIN_ROOT}/lib/state.sh"
source "${PLUGIN_ROOT}/lib/gemini-wrapper.sh"
source "${PLUGIN_ROOT}/lib/codex-wrapper.sh"

# Parse arguments with while-loop (Phase 2 decision: while-loop, not getopts)
while [[ $# -gt 0 ]]; do
  case "$1" in
    --flag) FLAG=true; shift ;;
    *)      DESCRIPTION="$1"; shift ;;
  esac
done

# Set hook depth guard (wrappers set this too, but belt-and-suspenders)
export DEVSQUAD_HOOK_DEPTH=1
```

**Project dir resolution:** All scripts use `CLAUDE_PROJECT_DIR` env var with fallback to `.`:
```bash
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-.}"
```

### Pattern 4: Gemini Codebase Research Invocation
**What:** `invoke_gemini_with_files` accepts space-separated `@path` tokens. The wrapper's `expand_dir_refs` function automatically expands `@dir/` into individual `@file` references for all `.sh`, `.md`, `.json` files.

**Verified from gemini-wrapper.sh:**
```bash
source "${PLUGIN_ROOT}/lib/gemini-wrapper.sh"

# Research existing skill patterns — pass @dir/ tokens; wrapper expands them
RESEARCH=$(invoke_gemini_with_files \
  "@${PLUGIN_ROOT}/skills/ @${PLUGIN_ROOT}/commands/" \
  "Analyze these DevSquad skill files. What is the exact SKILL.md frontmatter format? What shell script conventions are used? List the pattern for argument parsing, path resolution, and library sourcing. Under 400 words." \
  400 \
  90)
```

**Word limit convention:** Use non-zero word limit for research responses. Use `0` for code output (no truncation).

### Pattern 5: Codex Draft Invocation
**What:** `invoke_codex` takes a prompt string, optional line limit, and timeout. For code drafting, pass `0` as line limit to avoid truncation.

**Verified from codex-wrapper.sh and codex-developer.md:**
```bash
source "${PLUGIN_ROOT}/lib/codex-wrapper.sh"

DRAFT=$(invoke_codex "Generate a DevSquad skill for: ${DESCRIPTION}

Follow these conventions from the codebase research:
${RESEARCH}

Generate two files:
1. SKILL.md — frontmatter (name, description, version) + prose spec with ## When to Use, ## Usage, ## Dependencies
2. scripts/implement.sh — bash script following DevSquad conventions

Output as two sections clearly delimited by === FILE: filename === headers." \
  0 \
  120)
```

**Timeout:** Use 120s for code generation (longer than research).

### Pattern 6: Skill Registration / Discovery
**What:** There is NO registry file. Skills are discovered by name convention. A command stub in `plugin/commands/<name>.md` hardcodes `Invoke the devsquad:<skill-name> skill`. The `CLAUDE_PLUGIN_ROOT` env var is set by Claude Code at session start to the plugin root directory.

**Verified:** No `registry.json`, no dynamic loading, no auto-discovery scan. The relationship is:
```
/devsquad:generate  →  plugin/commands/generate.md  →  "Invoke devsquad:code-generation skill"
                                                        →  plugin/skills/code-generation/SKILL.md
                                                        →  plugin/skills/code-generation/scripts/generate-skill.sh
```

**To register a newly generated skill:** The `generate-skill.sh` script must write both the `SKILL.md` + scripts AND a corresponding `commands/<name>.md` stub. Without the command stub, the skill is present on disk but not invocable.

### Anti-Patterns to Avoid
- **Calling `gemini -p` directly:** Bypasses rate-limit tracking, hook-depth guard, and usage recording. Always source and use `invoke_gemini` or `invoke_gemini_with_files`.
- **Calling `codex exec` directly:** Same problem. Always use `invoke_codex`.
- **Generating code yourself inside the skill script:** The skill's job is to orchestrate Gemini and Codex, not to template-substitute. Claude doing the generation defeats the token-savings purpose.
- **Writing files without user confirmation:** Phase requirement CGEN-04 mandates user review before save. Never auto-write.
- **Using getopts for argument parsing:** Phase 2 decision locked in while-loop argument parsing for all shell scripts.
- **Hardcoding plugin root:** Always resolve via `BASH_SOURCE[0]` chain. `CLAUDE_PLUGIN_ROOT` may not be set in all execution contexts.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Gemini invocation with timeout/rate-limit | Custom `gemini -p` wrapper | `invoke_gemini_with_files` from `gemini-wrapper.sh` | Already handles cooldown, auth errors, timeout (gtimeout/timeout), hook depth, usage recording |
| Codex invocation with error taxonomy | Custom `codex exec` wrapper | `invoke_codex` from `codex-wrapper.sh` | Same taxonomy (RATE_LIMITED, AUTH_ERROR, TIMEOUT, CLI_ERROR) used by all agents |
| JSON state writes | `echo > file` | `write_state` from `state.sh` | Atomic write via tmp+rename pattern prevents corruption |
| Directory expansion for @file refs | Custom find loop | `expand_dir_refs` from `gemini-wrapper.sh` | Already handles `.ts/.js/.sh/.py/.go/.rs/.md/.json`, skips non-matching |
| Config reading | Direct file parse | `jq` with grep fallback (pattern from existing scripts) | Consistent with codebase; jq fallback already in every script |

**Key insight:** The wrappers are fully production-ready. The code generation skill's complexity is in the UX flow (research → draft → display → confirm → write), not in the tool invocation layer.

---

## Common Pitfalls

### Pitfall 1: CLAUDE_PLUGIN_ROOT Not Set in Subshells
**What goes wrong:** When `generate-skill.sh` is run via Bash tool (not as a Claude slash command), `CLAUDE_PLUGIN_ROOT` may be empty. Scripts that rely on it without fallback will fail with `bash: /skills/...: No such file or directory`.

**Why it happens:** `CLAUDE_PLUGIN_ROOT` is injected by Claude Code at session start. Direct Bash invocations don't inherit it.

**How to avoid:** Always resolve plugin root from `BASH_SOURCE[0]` as the primary method:
```bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PLUGIN_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
```
Use `CLAUDE_PLUGIN_ROOT` only as an override if set.

**Warning signs:** Script works in Claude Code session but fails when run from terminal.

### Pitfall 2: Codex Response Truncated by Default Line Limit
**What goes wrong:** `invoke_codex` defaults to 50-line responses (from config or hardcoded default). A SKILL.md + implementation script will exceed 50 lines. Codex output gets cut off, producing partial files.

**Why it happens:** The line limit is a token-saving measure designed for short code snippets. Code generation is an exception.

**How to avoid:** Always pass `0` as the line limit for code drafting:
```bash
invoke_codex "$PROMPT" 0 120
```

**Warning signs:** Generated files end abruptly mid-function or mid-section.

### Pitfall 3: Gemini Word Limit Cuts Off Pattern Research
**What goes wrong:** If the caller passes a small word limit (or relies on config default of 300), Gemini truncates its analysis of skill patterns before covering all conventions.

**Why it happens:** `invoke_gemini_with_files` appends `. Under N words.` if no word bound is present in the prompt.

**How to avoid:** Pass an explicit word limit of 400-500 for research, or include `. Under 500 words.` explicitly in the prompt so the wrapper doesn't override it.

### Pitfall 4: Writing Generated Files Without User Confirmation
**What goes wrong:** Skill overwrites existing files or creates incorrect directory structure without the user seeing the draft first.

**Why it happens:** Automation temptation — stream straight to disk.

**How to avoid:** Print the full draft to stdout, ask for confirmation (`[y/N]`), and only write on `y`. This is CGEN-04 compliance. Use `read -r CONFIRM` for interactive prompt.

**Warning signs:** User never sees what was generated before it's on disk.

### Pitfall 5: Generated Skill Not Immediately Invocable
**What goes wrong:** `generate-skill.sh` creates `plugin/skills/new-skill/` but does not create `plugin/commands/new-skill.md`. User tries `/devsquad:new-skill` and gets "command not found."

**Why it happens:** Discovery is not automatic — the command stub is required.

**How to avoid:** The script must always generate BOTH the skill directory AND the command stub. The command stub content is simple and templatable.

### Pitfall 6: Hook Recursion When Skill Uses Bash Tool
**What goes wrong:** When the code generation skill calls `gemini` or `codex` via Bash, the `PreToolUse` hook intercepts and suggests delegating... the delegation tool. Infinite suggestion loop.

**Why it happens:** `DEVSQUAD_HOOK_DEPTH` is the guard. If not set before the sub-invocation, hooks fire.

**How to avoid:** The wrappers set `export DEVSQUAD_HOOK_DEPTH=1` internally. Sourcing the wrappers and using `invoke_gemini`/`invoke_codex` is sufficient. Do NOT call `gemini -p` or `codex exec` directly.

---

## Code Examples

Verified patterns from codebase source files:

### Sourcing Wrappers and Invoking Gemini for Research
```bash
# Source: plugin/lib/gemini-wrapper.sh (verified)
source "${PLUGIN_ROOT}/lib/gemini-wrapper.sh"

RESEARCH=$(invoke_gemini_with_files \
  "@${PLUGIN_ROOT}/skills/ @${PLUGIN_ROOT}/commands/" \
  "Analyze these DevSquad skill files. What is the exact SKILL.md format? Shell script conventions for path resolution, argument parsing, library sourcing? Under 500 words." \
  500 \
  90)

if [[ $? -ne 0 ]]; then
  echo "$RESEARCH" >&2   # error message already formatted by wrapper
  exit 1
fi
```

### Sourcing Codex Wrapper and Drafting Skill Files
```bash
# Source: plugin/lib/codex-wrapper.sh (verified)
source "${PLUGIN_ROOT}/lib/codex-wrapper.sh"

DRAFT=$(invoke_codex \
  "Generate a DevSquad skill named '${SKILL_NAME}' that does: ${DESCRIPTION}

Use these conventions from codebase research:
${RESEARCH}

Output format:
=== FILE: SKILL.md ===
[frontmatter + prose spec]

=== FILE: scripts/${SKILL_NAME}.sh ===
[bash implementation]

=== FILE: commands/${SKILL_NAME}.md ===
[thin command stub]" \
  0 \
  120)

if [[ $? -ne 0 ]]; then
  echo "$DRAFT" >&2
  exit 1
fi
```

### Writing Files Atomically (state.sh pattern)
```bash
# Source: plugin/lib/state.sh write_state function (verified)
write_state() {
  local file_path="$1"
  local content="$2"
  local temp_file="${file_path}.tmp.$$"
  echo "$content" > "$temp_file"
  mv "$temp_file" "$file_path"
}
```

### Argument Parsing (while-loop, Phase 2 decision)
```bash
# Source: plugin/skills/git-health/scripts/git-health.sh (verified)
DESCRIPTION=""
SKILL_NAME=""
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name)    shift; SKILL_NAME="${1:-}"; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    *)         DESCRIPTION="${DESCRIPTION}${DESCRIPTION:+ }${1}"; shift ;;
  esac
done

if [[ -z "$DESCRIPTION" ]]; then
  echo "Usage: generate-skill.sh <description> [--name skill-name] [--dry-run]" >&2
  exit 1
fi
```

### SKILL.md Template Structure
```markdown
---
name: code-generation
description: This skill should be used when the user asks to "generate a skill", "create a new command", "build a devsquad skill", or describes a new automation they want as a slash command.
version: 1.0.0
---

# Code Generation Skill

Transforms a natural language description into a working DevSquad skill by researching existing patterns with Gemini and drafting implementation with Codex.

## When to Use
...

## Usage
\`\`\`bash
bash "${CLAUDE_PLUGIN_ROOT}/skills/code-generation/scripts/generate-skill.sh" "<description>"
\`\`\`

## Dependencies
- lib/gemini-wrapper.sh — Codebase pattern research
- lib/codex-wrapper.sh — Implementation drafting
- lib/state.sh — Atomic file writes
```

### Command Stub Template
```markdown
---
description: Generate a new DevSquad skill from a natural language description
argument-hint: "<description of the skill you want to create>"
allowed-tools: ["Read", "Write", "Bash", "Skill"]
---

# DevSquad Generate

Arguments: $ARGUMENTS

Invoke the devsquad:code-generation skill with the provided description and follow it exactly.
```

### User Confirmation Pattern
```bash
# Print draft for review
echo ""
echo "=== GENERATED DRAFT ==="
echo "$DRAFT"
echo "========================"
echo ""

# Prompt for confirmation
read -r -p "Write these files? [y/N] " CONFIRM
if [[ "${CONFIRM,,}" != "y" && "${CONFIRM,,}" != "yes" ]]; then
  echo "Aborted. No files written."
  exit 0
fi
```

---

## UX Flow: Input to Invocable Skill

The recommended flow for `generate-skill.sh`:

```
1. Validate input
   └── $1 must be non-empty description

2. Derive skill-name from description (or accept --name flag)
   └── sanitize: lowercase, spaces to hyphens, strip special chars

3. Research phase (Gemini)
   └── invoke_gemini_with_files "@plugin/skills/ @plugin/commands/"
       "Analyze patterns. Under 500 words."
   └── On failure: print wrapper error, exit 1

4. Draft phase (Codex)
   └── invoke_codex with RESEARCH context in prompt, line_limit=0, timeout=120
   └── On failure: print wrapper error, exit 1

5. Review phase (interactive)
   └── Print full draft to terminal
   └── read -r CONFIRM (y/N)
   └── On N: exit 0 (no files written)

6. Write phase
   └── mkdir -p plugin/skills/${SKILL_NAME}/scripts/
   └── Parse draft sections by === FILE: ... === delimiter
   └── write_state for each file (atomic)
   └── chmod +x scripts/*.sh

7. Verify phase
   └── bash -n plugin/skills/${SKILL_NAME}/scripts/*.sh
   └── Print pass/fail

8. Summary
   └── Print: files written, command to invoke, next steps
```

---

## State of the Art

| Old Approach | Current Approach | Notes |
|--------------|-----------------|-------|
| N/A — no generate skill exists | New in Phase 3 | First skill that generates other skills |
| getopts argument parsing | while-loop parsing | Phase 2 decision — locked |
| Direct gemini/codex CLI calls | Wrapper sourcing pattern | Established in Phase 1/2 |

---

## Open Questions

1. **Skill name derivation from description**
   - What we know: No existing auto-naming in the codebase
   - What's unclear: Whether to derive name from description automatically or always require `--name`
   - Recommendation: Support both — auto-derive with spaces-to-hyphens, allow `--name` override

2. **Draft parsing delimiter**
   - What we know: Codex output is free-form text; we need a reliable delimiter to split SKILL.md from script content
   - What's unclear: How consistently Codex follows delimiter instructions
   - Recommendation: Use `=== FILE: <name> ===` as delimiter; add fallback to write raw output as single file if delimiter not found, with warning

3. **Iterative editing after review**
   - What we know: CGEN-04 says "present draft for user review/iteration"
   - What's unclear: Does "iteration" mean re-running Codex with feedback, or just manual edit after writing?
   - Recommendation: Plan for simple re-run loop — after user sees draft, offer `[y]es / [n]o / [e]dit prompt` three-way choice

4. **CGEN-05: Skill that generates skills**
   - What we know: The code-generation skill IS the self-referential skill (generates skill scaffolding for any description)
   - What's unclear: Whether CGEN-05 means something additional beyond the skill itself
   - Recommendation: Verify with user whether CGEN-05 is satisfied by `generate-skill.sh` or requires a separate meta-skill

---

## Sources

### Primary (HIGH confidence)
- `/Users/Dikshant/Desktop/Projects/devsquad/plugin/lib/gemini-wrapper.sh` — Full source read; `invoke_gemini`, `invoke_gemini_with_files`, `expand_dir_refs` API verified
- `/Users/Dikshant/Desktop/Projects/devsquad/plugin/lib/codex-wrapper.sh` — Full source read; `invoke_codex` API, error taxonomy verified
- `/Users/Dikshant/Desktop/Projects/devsquad/plugin/lib/routing.sh` — Full source read; routing pattern, category names verified
- `/Users/Dikshant/Desktop/Projects/devsquad/plugin/lib/state.sh` — Full source read; `write_state`, `init_state_dir` verified
- `/Users/Dikshant/Desktop/Projects/devsquad/plugin/skills/devsquad-config/SKILL.md` — SKILL.md format verified
- `/Users/Dikshant/Desktop/Projects/devsquad/plugin/skills/devsquad-dispatch/SKILL.md` — dispatch skill format verified
- `/Users/Dikshant/Desktop/Projects/devsquad/plugin/skills/onboarding/SKILL.md` — onboarding skill format verified
- `/Users/Dikshant/Desktop/Projects/devsquad/plugin/skills/git-health/SKILL.md` — git-health format verified
- `/Users/Dikshant/Desktop/Projects/devsquad/plugin/skills/git-health/scripts/git-health.sh` — argument parsing pattern verified
- `/Users/Dikshant/Desktop/Projects/devsquad/plugin/commands/config.md` — command stub format verified
- `/Users/Dikshant/Desktop/Projects/devsquad/plugin/commands/status.md` — command stub format verified
- `/Users/Dikshant/Desktop/Projects/devsquad/plugin/commands/setup.md` — command stub format verified
- `/Users/Dikshant/Desktop/Projects/devsquad/plugin/hooks/hooks.json` — hook registration format verified; confirmed no registry for skills
- `/Users/Dikshant/Desktop/Projects/devsquad/plugin/agents/codex-developer.md` — Codex invocation conventions verified
- `/Users/Dikshant/Desktop/Projects/devsquad/plugin/agents/gemini-developer.md` — Gemini invocation conventions verified
- `/Users/Dikshant/Desktop/Projects/devsquad/.planning/phases/03-enforcement-and-routing-skills/03-UAT.md` — Phase 3 scope confirmed (UAT exists, no PLAN yet)

### Secondary (MEDIUM confidence)
- `/Users/Dikshant/Desktop/Projects/devsquad/.planning/phases/01-delegation-advisor/01-RESEARCH.md` — Prior research format reference; hook architecture cross-reference

---

## Metadata

**Confidence breakdown:**
- Skill anatomy (SKILL.md format, scripts layout): HIGH — read 5 skill directories directly
- Wrapper APIs (invoke_gemini, invoke_codex signatures): HIGH — full source read
- Command stub format: HIGH — read 3 command stubs
- Registration/discovery mechanism: HIGH — confirmed no registry; hooks.json shows only hook registration
- UX flow design: MEDIUM — derived from requirements + codebase patterns; no existing generate skill to reference
- Draft parsing (delimiter reliability): LOW — Codex output consistency with delimiter instructions not verified empirically

**Research date:** 2026-02-18
**Valid until:** 2026-03-18 (stable codebase; wrappers unlikely to change)
