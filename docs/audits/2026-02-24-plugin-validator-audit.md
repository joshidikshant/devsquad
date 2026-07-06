# DevSquad Logic Audit for Claude

Generated on: 2026-02-24
Repository: /Users/Dikshant/Desktop/Projects/devsquad

## Scope

This document combines two requested audits:

1. Release-readiness audit based on the Anthropic `plugin-dev` guidance:
   https://github.com/anthropics/claude-code/blob/main/plugins/plugin-dev/README.md
2. Slash-command namespace audit for `/devsquad:{command}` consistency.

## Audit Method

- Static review of manifests, hooks, commands, skills, shell libraries, and documentation.
- Runtime validations and repros using local CLI/tooling where possible.
- No source code changes were made as part of the audit.

Commands run for validation included:

- `claude plugin validate plugin/.claude-plugin/plugin.json`
- `claude plugin validate .claude-plugin/marketplace.json`
- `claude plugin validate plugin`
- `bash -n` over all shell scripts under `plugin/`
- `jq empty` over all JSON files
- Focused runtime repros for workflow failure handling and status JSON behavior

## Audit 1: Release Readiness (plugin-dev basis)

### Critical Findings (P1)

1. Workflow failure handling is incorrect and can checkpoint failed steps.

- Evidence:
  - `plugin/skills/workflow-orchestration/scripts/run-workflow.sh:167`
  - `plugin/skills/workflow-orchestration/scripts/run-workflow.sh:184`
- Issue:
  - Step execution uses `if ! cmd; then STEP_EXIT=$?`, so `$?` becomes the status of `!` and can report `0` in failure paths.
  - Checkpoint gating then treats failed steps as successful.
- Repro evidence:
  - Runtime output showed `ERROR: Step 'failing-step' failed (exit 0).`

2. Built-in workflow template is not reliably runnable as shipped.

- Evidence:
  - `plugin/skills/workflow-orchestration/templates/feature-workflow.json:9`
  - `plugin/skills/workflow-orchestration/templates/feature-workflow.json:17`
  - `plugin/skills/workflow-orchestration/templates/feature-workflow.json:20`
  - `plugin/skills/workflow-orchestration/templates/feature-workflow.json:32`
  - `plugin/skills/workflow-orchestration/scripts/run-workflow.sh:129`
  - `plugin/skills/workflow-orchestration/scripts/run-workflow.sh:167`
- Issues:
  - Template relies on variables (`$FEATURE_NAME`, `$FEATURE_DESCRIPTION`, `$PLUGIN_ROOT`) that are not guaranteed/exported by the runner.
  - Step execution is unquoted (`${STEP_SKILL} ${STEP_ARGS}`), so JSON argument quoting is not preserved safely.
  - Cleanup step is modeled as `skill: "bash"` + `args: "rm -f ... || true"`, which effectively becomes `bash rm ...` instead of a valid script or `bash -lc` form.

3. `/devsquad:status --json` is not pure JSON.

- Evidence:
  - `plugin/lib/state.sh:13`
  - `plugin/skills/devsquad-status/scripts/show-status.sh:15`
- Issue:
  - `init_state_dir` prints the state path to stdout and `show-status.sh` calls it without capturing output.
  - Machine-readable mode emits a path line before JSON, which breaks JSON parsers.

4. Bash 3 compatibility break in code-generation flow.

- Evidence:
  - `plugin/skills/code-generation/scripts/generate-skill.sh:270`
- Issue:
  - Uses `${REPLY,,}` lowercasing, which is not supported by bash 3.2 (default macOS system bash).
- Repro evidence:
  - `bash: ${REPLY,,}: bad substitution`

### Important Findings (P2)

5. Documentation/changelog claims model selection behavior that is not present in wrappers.

- Evidence:
  - `README.md:208`
  - `README.md:219`
  - `CHANGELOG.md:8`
  - `plugin/lib/gemini-wrapper.sh:118`
  - `plugin/lib/codex-wrapper.sh:72`
- Issue:
  - Docs claim `gemini_model` / `codex_model` are read and passed as `-m`.
  - Wrapper invocations do not currently pass model flags.
  - `/devsquad:config gemini_model=...` currently fails as unknown key.

6. `default_routes.research=codex` is allowed by config validation/docs but not implemented by routing.

- Evidence:
  - `plugin/skills/devsquad-config/SKILL.md:40`
  - `plugin/skills/devsquad-config/scripts/update-config.sh:86`
  - `plugin/lib/routing.sh:60`
- Issue:
  - Config UI/validation allows `codex` for research.
  - Routing logic supports only Gemini or self for research.

7. Version metadata is inconsistent across release artifacts.

- Evidence:
  - `plugin/.claude-plugin/plugin.json:3` (0.2.0)
  - `.claude-plugin/marketplace.json:8` (0.1.0)
  - `.claude-plugin/marketplace.json:15` (0.1.0)
  - `CHANGELOG.md:5` (2.1.0)
- Issue:
  - Published version signals are contradictory and can confuse install/update expectations.

### Minor Finding (P3)

8. README contains incorrect Gemini package name in prerequisites.

- Evidence:
  - `README.md:52`
- Issue:
  - Uses `@anthropic-ai/gemini-cli` instead of `@google/gemini-cli`.

### Validation Results That Passed

- Plugin and marketplace manifests passed `claude plugin validate`.
- All shell scripts passed `bash -n`.
- All JSON files parsed successfully with `jq`.

### Release Verdict (Audit 1)

Not release-ready until P1 issues are fixed.

## Audit 2: Slash Command Namespace Consistency

### Objective

Verify whether commands follow `/devsquad:{command}` or use mixed/random structure.

### Command Inventory (from `plugin/commands/*.md`)

- `setup` -> `/devsquad:setup`
- `status` -> `/devsquad:status`
- `config` -> `/devsquad:config`
- `capacity` -> `/devsquad:capacity`
- `generate` -> `/devsquad:generate`
- `git-health` -> `/devsquad:git-health`
- `workflow` -> `/devsquad:workflow`

Evidence:

- `plugin/commands/setup.md:2`
- `plugin/commands/status.md:2`
- `plugin/commands/config.md:2`
- `plugin/commands/capacity.md:2`
- `plugin/commands/generate.md:2`
- `plugin/commands/git-health.md:2`
- `plugin/commands/workflow.md:2`

Docs are consistent with this namespace:

- `README.md:83`
- `plugin/README.md:85`

### Findings

- Current built-in plugin commands are consistently namespaced under `/devsquad:{name}`.
- Non-`/devsquad` slash commands found in docs are external references (`/status`, `/stats`, `/gsd:*`, `/tribe:*`, etc.), not DevSquad command definitions.

### Namespace Caveat

Generated command template in code-generation currently omits `name:` in command frontmatter:

- `plugin/skills/code-generation/scripts/generate-skill.sh:140`
- `plugin/skills/code-generation/scripts/generate-skill.sh:366`

This does not affect currently shipped built-ins, but it can produce malformed future generated commands.

### Verdict (Audit 2)

Namespace compliance is good for current shipped commands: no random command structure detected in built-ins.

## Consolidated Recommendation Before Release

Release should be blocked until P1 issues are resolved and re-validated. Namespace structure is acceptable, but generated-command template robustness should be fixed before encouraging `/devsquad:generate` for production extension workflows.
