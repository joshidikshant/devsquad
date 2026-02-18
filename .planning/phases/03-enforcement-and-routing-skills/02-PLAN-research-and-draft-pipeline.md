---
phase: 03-enforcement-and-routing-skills
plan: 02
type: execute
wave: 2
depends_on:
  - 03-01
files_modified:
  - plugin/skills/code-generation/scripts/generate-skill.sh
autonomous: true
requirements:
  - CGEN-02
  - CGEN-03

must_haves:
  truths:
    - "Running generate-skill.sh with a description triggers Gemini to analyze plugin/skills/ and plugin/commands/"
    - "Gemini research output is captured and passed as context to Codex draft prompt"
    - "Codex draft uses line_limit=0 to avoid truncation of multi-file output"
    - "Draft output contains three delimited sections: SKILL.md, scripts/<name>.sh, commands/<name>.md"
    - "On Gemini failure, script prints wrapper error and exits 1 (no silent continuation)"
    - "On Codex failure, script prints wrapper error and exits 1"
  artifacts:
    - path: "plugin/skills/code-generation/scripts/generate-skill.sh"
      provides: "Script with working research and draft phases (stubs replaced)"
      contains: "invoke_gemini_with_files"
    - path: "plugin/skills/code-generation/scripts/generate-skill.sh"
      provides: "Codex invocation with line_limit=0"
      contains: "invoke_codex"
  key_links:
    - from: "plugin/skills/code-generation/scripts/generate-skill.sh"
      to: "plugin/lib/gemini-wrapper.sh"
      via: "invoke_gemini_with_files call"
      pattern: "invoke_gemini_with_files"
    - from: "plugin/skills/code-generation/scripts/generate-skill.sh"
      to: "plugin/lib/codex-wrapper.sh"
      via: "invoke_codex call with RESEARCH in prompt"
      pattern: "invoke_codex"
---

<objective>
Replace the research and draft stubs in generate-skill.sh with real implementations. Phase 1 (Gemini research) calls invoke_gemini_with_files pointing at plugin/skills/ and plugin/commands/ to extract patterns. Phase 2 (Codex draft) passes the research summary in its prompt and calls invoke_codex with line_limit=0, producing delimited output for three files.

Purpose: Implements CGEN-02 and CGEN-03. After this plan, running generate-skill.sh produces a complete multi-file draft from any description.
Output: Updated generate-skill.sh with functional research and draft phases.
</objective>

<execution_context>
@/Users/Dikshant/.claude/get-shit-done/workflows/execute-plan.md
@/Users/Dikshant/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/phases/03-enforcement-and-routing-skills/03-01-SUMMARY.md
@plugin/skills/code-generation/scripts/generate-skill.sh
@plugin/lib/gemini-wrapper.sh
@plugin/lib/codex-wrapper.sh
</context>

<tasks>

<task type="auto">
  <name>Task 1: Implement Gemini research phase</name>
  <files>plugin/skills/code-generation/scripts/generate-skill.sh</files>
  <action>
Replace the Phase 1 research stub block in generate-skill.sh. The stub is:

```bash
# STUB — replaced by Plan 02
RESEARCH="[research stub — Plan 02 will implement]"
```

Replace it with:

```bash
RESEARCH=$(invoke_gemini_with_files \
  "@${PLUGIN_ROOT}/skills/ @${PLUGIN_ROOT}/commands/" \
  "Analyze these DevSquad skill and command files. Extract the exact SKILL.md frontmatter fields (name, description, version) and prose structure (## When to Use, ## Usage, ## Dependencies sections). Extract the shell script conventions: path resolution via BASH_SOURCE, library sourcing order (state.sh first, then wrappers), argument parsing with while-loop. List how the command stub in commands/ references the skill. Under 500 words." \
  500 \
  90)

if [[ $? -ne 0 ]]; then
  echo "Error: Gemini research failed." >&2
  echo "$RESEARCH" >&2
  exit 1
fi

echo "  Research complete (${#RESEARCH} chars)"
```

Key implementation notes:
- Uses invoke_gemini_with_files (not gemini -p directly) — wrapper handles rate-limit, hook depth guard, usage tracking
- Word limit 500 is explicit in prompt AND passed as 4th arg — wrapper uses whichever is lower
- @dir/ tokens are expanded by expand_dir_refs inside the wrapper to individual @file references
- Exit code check uses $? immediately after invoke_gemini_with_files (bash -e can't catch subshell failures reliably)
  </action>
  <verify>grep -q "invoke_gemini_with_files" plugin/skills/code-generation/scripts/generate-skill.sh && echo "gemini call present"
bash -n plugin/skills/code-generation/scripts/generate-skill.sh && echo "syntax OK"
grep -q "research stub" plugin/skills/code-generation/scripts/generate-skill.sh && echo "FAIL: stub not replaced" || echo "stub removed OK"</verify>
  <done>invoke_gemini_with_files appears in the script. bash -n passes. The "[research stub]" string is no longer in the file.</done>
</task>

<task type="auto">
  <name>Task 2: Implement Codex draft phase with three-file delimiter output</name>
  <files>plugin/skills/code-generation/scripts/generate-skill.sh</files>
  <action>
Replace the Phase 2 draft stub block in generate-skill.sh. The stub is:

```bash
# STUB — replaced by Plan 02
DRAFT="[draft stub — Plan 02 will implement]"
```

Replace it with:

```bash
DRAFT=$(invoke_codex \
"Generate a complete DevSquad skill named '${SKILL_NAME}' that does: ${DESCRIPTION}

Use EXACTLY these conventions observed in the codebase:
${RESEARCH}

Output three files using these EXACT delimiters (no extra text before or after each section):

=== FILE: SKILL.md ===
---
name: ${SKILL_NAME}
description: [one-sentence activation trigger — describe when Claude should use this skill]
version: 1.0.0
---

# [Skill Title]

[What the skill does]

## When to Use
[bullet list of triggers]

## Usage
\`\`\`bash
bash \"\${CLAUDE_PLUGIN_ROOT}/skills/${SKILL_NAME}/scripts/${SKILL_NAME}.sh\" [arguments]
\`\`\`

## Dependencies
- lib/state.sh — [purpose]
[add other libs as needed]

=== FILE: scripts/${SKILL_NAME}.sh ===
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=\"\$(cd \"\$(dirname \"\${BASH_SOURCE[0]:-\$0}\")\" && pwd)\"
PLUGIN_ROOT=\"\$(cd \"\${SCRIPT_DIR}/../../..\" && pwd)\"

source \"\${PLUGIN_ROOT}/lib/state.sh\"
[add other sources as needed]

[implementation using while-loop argument parsing, following codebase conventions]

=== FILE: commands/${SKILL_NAME}.md ===
---
description: [one-line command description]
argument-hint: \"[argument hint]\"
allowed-tools: [\"Read\", \"Write\", \"Bash\", \"Skill\"]
---

# DevSquad $(echo "${SKILL_NAME}" | sed 's/-/ /g' | sed 's/\b./\u&/g')

Arguments: \$ARGUMENTS

Invoke the devsquad:${SKILL_NAME} skill and follow it exactly." \
  0 \
  120)

if [[ $? -ne 0 ]]; then
  echo "Error: Codex draft failed." >&2
  echo "$DRAFT" >&2
  exit 1
fi

echo "  Draft complete ($(echo "$DRAFT" | wc -l | tr -d ' ') lines)"
```

Key implementation notes:
- line_limit=0 is REQUIRED — default 50 lines will truncate multi-file output (Pitfall 2)
- timeout=120 for code generation (longer than 90s research timeout)
- RESEARCH variable embedded in prompt relays Gemini's codebase context to Codex
- Delimiter format "=== FILE: <name> ===" used for parsing in Plan 03
- Prompt includes template scaffolding so Codex produces structurally correct output even with minimal research
  </action>
  <verify>grep -q "invoke_codex" plugin/skills/code-generation/scripts/generate-skill.sh && echo "codex call present"
grep -q "line_limit=0\|\" 0 \"" plugin/skills/code-generation/scripts/generate-skill.sh || grep -A2 "invoke_codex" plugin/skills/code-generation/scripts/generate-skill.sh | grep -q "0" && echo "line limit check"
bash -n plugin/skills/code-generation/scripts/generate-skill.sh && echo "syntax OK"
grep -q "draft stub" plugin/skills/code-generation/scripts/generate-skill.sh && echo "FAIL: stub not replaced" || echo "stub removed OK"</verify>
  <done>invoke_codex appears in the script. The codex call passes 0 as line_limit. bash -n passes. The "[draft stub]" string is no longer in the file.</done>
</task>

</tasks>

<verification>
1. bash -n plugin/skills/code-generation/scripts/generate-skill.sh passes
2. grep confirms invoke_gemini_with_files present with @${PLUGIN_ROOT}/skills/ and @${PLUGIN_ROOT}/commands/ tokens
3. grep confirms invoke_codex present with 0 as line_limit argument
4. grep confirms RESEARCH variable is referenced in the Codex prompt string
5. Neither "[research stub]" nor "[draft stub]" strings remain in the file
6. Error handling: both invocations check exit code and exit 1 on failure
</verification>

<success_criteria>
- generate-skill.sh research phase calls invoke_gemini_with_files (not raw gemini CLI)
- generate-skill.sh draft phase calls invoke_codex with line_limit=0 and embeds RESEARCH in prompt
- Codex prompt instructs three-file delimited output ("=== FILE: ... ===")
- Both phases exit 1 with error message on invocation failure
- CGEN-02 satisfied: skill uses Gemini to research codebase patterns
- CGEN-03 satisfied: skill uses Codex to draft command implementation
</success_criteria>

<output>
After completion, create .planning/phases/03-enforcement-and-routing-skills/03-02-SUMMARY.md with:
- Files modified (1 file)
- Research prompt used (exact text)
- Codex draft delimiter format used ("=== FILE: ... ===")
- Error handling approach
- Verification results
</output>
