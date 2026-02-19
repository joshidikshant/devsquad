# Changelog

All notable changes to DevSquad are documented here.

## [2.0.0] — 2026-02-19

### Added
- **Delegation Advisor**: Hook fires when Claude reads 3+ files in a session — suggests Gemini delegation with estimated token savings (per-file and cumulative)
- **Acceptance tracking**: Heuristic correlation tracks which delegation suggestions users accept vs decline; metrics reported via `/devsquad:status`
- **Git Health Check skill**: `/devsquad:git-health` — detects broken symlinks, orphaned branches, and uncommitted changes; supports `--json` output (`total_issues` integer) and `--check <category>` for targeted scans
- **Code Generation skill**: `/devsquad:generate <description>` — full pipeline: Gemini researches existing patterns → Codex drafts skill → `[y/N/e]` review prompt → files written → `bash -n` syntax validation
- **Workflow Orchestration engine**: `run-workflow.sh` — executes multi-step JSON workflow definitions with destructive gates, git checkpoints, post-workflow validation, and `--dry-run` mode
- **lib-workflow.sh**: Shared helper library — `workflow_gate` (interactive confirm), `workflow_checkpoint` (git commit + state.json), `workflow_validate` (health check + optional test command)
- **feature-workflow.json**: Built-in workflow template — branch create → generate skill → validate → cleanup staging

### Fixed
- `workflow_validate` now reads `total_issues` (integer) from `git-health --json` instead of non-existent `.status` field — post-workflow validation no longer always reports failure
- `feature-workflow.json` step paths use `$PLUGIN_ROOT`-absolute references instead of CWD-relative paths — workflow runs correctly from any invocation directory

## [1.1.0] — 2026-02-12

### Fixed
- **Control-plane truthfulness**: Config values (`default_routes`, `gemini_word_limit`, `codex_line_limit`) now control runtime behavior
- **Compliance metrics**: Overrides are tracked separately from suggestions — compliance rate reflects actual user behavior
- **Nested state updates**: Fixed `session.zone` writing as literal top-level key instead of nested path
- **Route vocabulary**: Normalized `self` vs `claude` inconsistency across config, routing, and commands
- **Self-call metric**: `self_calls` counter now increments correctly

### Improved
- **Strict mode safety**: Enforcement falls back to advisory when target agent CLI is unavailable
- **Gemini agent prompts**: Rewritten to pass directories directly to Gemini instead of pre-reading files in Claude's context
- **Directory expansion**: Gemini wrapper now expands `@dir/` into individual file references
- **Codex fallback messages**: Task-type-aware fallback guidance (developer vs tester)
- **Failure telemetry**: Failed invocations now logged with input size for ROI tracking
- **Codex tester routing**: Bash test commands intercepted and routed to codex-tester

## [1.0.0] — 2026-02-11

### Added
- **Plugin skeleton**: `.claude-plugin/plugin.json` manifest, portable architecture with `${CLAUDE_PLUGIN_ROOT}`
- **Hook enforcement**: `SessionStart`, `PreToolUse`, `PreCompact`, `Stop` hooks
- **Agent wrappers**: Gemini CLI wrapper (with rate-limit backoff, timeout, auth handling) and Codex CLI wrapper (with exec mode, error classification)
- **Agent personas**: 6 agent definitions — `gemini-developer`, `gemini-reader`, `gemini-researcher`, `gemini-tester`, `codex-developer`, `codex-tester`
- **Routing engine**: Keyword-based task classification with configurable default routes
- **Usage tracking**: Per-session and aggregate stats for Claude, Gemini, and Codex invocations
- **Budget zones**: Green/yellow/red zones based on daily token volume with zone-specific behavior guidance
- **Slash commands**: `/devsquad:setup`, `/devsquad:config`, `/devsquad:status`
- **Onboarding skill**: Interactive first-run setup with environment detection
- **Session state**: State persistence across context compaction boundaries
- **Enforcement modes**: Advisory (suggest delegation) and Strict (block and require delegation)
