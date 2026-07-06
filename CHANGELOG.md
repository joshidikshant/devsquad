# Changelog

All notable changes to DevSquad are documented here.

> **Note:** Project was renumbered from 2.x to 0.x semver in Feb 2026 to reflect pre-stable status. Entries below have been renumbered accordingly.

## [0.6.0] — 2026-07-06

### Added — Grok Build integration (3rd CLI; triggers the D4 wrapper contract)
- **`lib/grok-wrapper.sh`**: `invoke_grok(prompt, word_limit, timeout)` with the shared wrapper contract — RATE_LIMITED/AUTH_ERROR/TIMEOUT/CLI_ERROR prefixes, auth-before-rate classification, cooldown, telemetry, contract logging, per-agent model resolution (`agent_models.<agent>` > `preferences.grok_model` > CLI default via `grok -m`). Catches Grok's exit-0 "Signing in with Grok..." banner as AUTH_ERROR (unauthenticated grok would otherwise record garbage as success)
- **Agents**: `grok-researcher` (live web/X research) and `grok-developer` (drafts/prototypes), both `model: sonnet` shells
- **Routing**: `grok` is now a valid `default_routes` target for research, development, code_generation, and testing (defaults unchanged — opt-in via config)
- **Detection/status**: grok in `detect_all_clis`, SessionStart squad status, `/devsquad:status` activity + availability; `~/.grok/bin` PATH bootstrap for hook subshells; `grok_calls` in session stats; `preferences.grok_word_limit` (default 300)
- **D4 conformance test** (`test/test_wrapper_contract.sh`): offline contract test over fake CLI binaries that all three wrappers must pass — success/auth/rate/telemetry/classification-order (the 'migrate' trap) plus the grok banner case

### Notes
- Grok has no native print-timeout flag (unlike agy) — on hosts without `timeout`/`gtimeout`, grok calls are bounded only by the CLI itself
- Requires one-time `grok login`; unauthenticated state is detected and reported as AUTH_ERROR

## [0.5.0] — 2026-07-06

### Breaking / Migration
- **Antigravity CLI is the only Gemini-role backend.** Google decommissioned the open-source Gemini CLI (stopped serving 2026-06-18). Wrappers, detection, status, and session-start now resolve `agy`/`antigravity` only and fail fast with `brew install --cask antigravity-cli` guidance; a legacy `gemini` binary on PATH no longer counts as available.

### Added
- **Model selection (real this time)**: `preferences.gemini_model` → `agy --model`, `preferences.codex_model` → `codex exec -m`; both keys creatable via `/devsquad:config` on configs from older templates
- **Contract validation logging** (`log_contract_check`): word/line bounds parsed from prompts, measured against responses, logged to `.devsquad/logs/contracts.log` (observe-only; enforcement gated on the D1 holdout verdict)
- **Context-occupancy zone** (`calculate_context_zone`): measured from the session transcript's last usage record; drives the read threshold instead of daily output volume (which was both the wrong proxy and a dead data source — stats-cache.json stopped updating 2026-06-16)
- **Advisory back-off**: at most 3 suggestions injected per session, then log-only (`advisory_capped`)
- **Per-session counters**: read/suggestion counters scoped by `session_id`; concurrent sessions no longer share or clobber counts
- **Test suite**: `bash test/run.sh` — 40+ assertions over `route_task()` and the PreToolUse hook (keywords, config overrides, jq-less mode, acceptance outcomes, context zone, cap, session isolation)
- **ROUTING-CHANGELOG.md**: routing table changes are now dated, human-edited entries (decision D2)

### Fixed
- **Error classification order**: auth checked before rate in both wrappers; rate regex tightened to `429|rate.?limit|quota|…` — Google's decommission notice ("mig**rate**") was being classified as RATE_LIMITED, converting a permanent failure into infinite retry
- **`invoke_gemini_with_files` telemetry**: the flagship gemini-reader path now records usage/stats (was fully invisible to `/devsquad:status`)
- **`printf %b` content corruption**: piped file content no longer has its backslash escapes expanded
- **Acceptance tracking honesty**: `Task` added to the hook matcher; `accepted` only when the invoked subagent matches the suggestion; same-tool = declined; anything else = `unresolved` (previously ANY different tool counted as accepted)
- **`update-config.sh`**: `jq -e` gate no longer rejects present-but-false boolean keys; known later-version keys are creatable with per-key validation and correct boolean typing
- **`write_state`** creates the parent directory — hook and routing paths no longer crash before SessionStart has initialized state
- **EXIT traps** in wrappers guard `${stderr_file:-}` (successful calls could exit non-zero under `set -u`)
- **`development` route** added to config defaults, `ensure_config`, and schema docs (existed only in routing code)
- **Read thresholds reconciled** to 20 (green) / 8 (context pressure) — v0.3.0 shipped 40/20, unreleased 0.4.0 had 3/1 which fires on trivial work
- **Per-agent model routing**: new top-level `agent_models` config map — each agent exports `DEVSQUAD_AGENT` and the wrappers resolve per-agent > global > CLI default, passing `agy --model` / `codex exec -m`. Antigravity multiplexes Gemini 3.5 Flash / 3.1 Pro / Claude Sonnet 4.6 / Claude Opus 4.6 / GPT-OSS 120B (`agy models`). `/devsquad:config` validates model names against `agy models` because agy silently ignores unknown names. All agy calls now carry `--print-timeout <timeout>s` (native bound — hosts without timeout/gtimeout previously had NO timeout at all)
- **Agent shells normalized to `model: sonnet`** (codex agents were `inherit`, i.e. billed at the session model)
- `.devsquad/` state dirs are now self-gitignoring (no more untracked noise in every project)
- Agent docs warn that wrappers must run via `bash -c` (Bash tool may execute under zsh, where sourcing bash-only libs breaks)

### Honesty
- README no longer claims "not suggestions" (default is advisory) and labels savings as unreconciled heuristics; measured Claude output ratio is ~2.7 chars/token, not the /4 the estimates assume

## [0.4.0] — 2026-04-16

### Fixed
- **Hook enforcement not active after `/devsquad:setup`**: Onboarding skill now includes Step 3.5 that registers all 4 DevSquad hooks (`SessionStart`, `PreToolUse`, `PreCompact`, `Stop`) into the project-scoped `.claude/settings.json` immediately after config is saved — hooks were previously only installed at global install time, leaving new projects unenforced
- **`generate-claude-md.sh` exit 127**: Fixed unquoted `${CLAUDE_PLUGIN_ROOT}` path in `claude-md-logic.md` that caused bash to expand to an empty path in non-interactive shells; also added `:-$(pwd)` fallback for unset `CLAUDE_PROJECT_DIR`
- **`install.sh` scope confusion**: Global `~/.claude/settings.json` hook registration (install-time) and project-scoped `.claude/settings.json` hook registration (setup-time) are now clearly separated; install.sh comment and completion message explain the two-phase design
- **`gemini-wrapper.sh` — `invoke_gemini_with_files` file sandbox bypass**: Rewrote to pipe file content via stdin instead of `@file` tokens, bypassing Gemini CLI's workspace sandbox restriction that prevented cross-project file reads
- **`gemini-wrapper.sh` — interactive prompt blocking**: Added `-y` flag to all `gemini` invocations to suppress confirmation prompts in non-interactive hook subshells
- **`gemini-wrapper.sh` / `cli-detect.sh` — NVM PATH missing in hooks**: Added NVM PATH bootstrap to both files so `gemini` and `codex` installed via NVM are discoverable in non-interactive subshells where `.zshrc`/`.bash_profile` are not sourced

### Improved
- **Onboarding Step 5**: Added hook verification step that checks all 4 DevSquad hook events are present in `~/.claude/settings.json` and surfaces actionable fix if any are missing

## [0.3.0] — 2026-02-25

### Added
- **Model selection for Gemini and Codex**: Wrappers now read `gemini_model` and `codex_model` from `.devsquad/config.json` and pass `-m <model>` to both CLIs. Defaults to `gemini-3-pro` and `gpt-5.3-codex`. Configurable per-project via `/devsquad:config gemini_model=<name>`

### Fixed
- **`hooks.json` format**: Removed non-standard `"hooks"` wrapper key and `"description"` field — event names are now top-level keys matching the Claude Code plugin spec
- **Command frontmatter**: Added missing `name:` field to all 6 commands that lacked it (`setup`, `status`, `config`, `workflow`, `generate`, `git-health`)
- **`update-config.sh`**: Fixed undefined `$jq_path` variable; consolidated broken nested-key fallback into a single jq path expression
- **`pre-tool-use.sh`**: Removed invalid `local` keyword used outside a function

### Improved (code simplification — net -353 lines)
- **`gemini-wrapper.sh`**: Removed duplicated `_resolve_state_dir()` (already in `state.sh`); simplified cooldown date formatting to chained `||` fallback
- **`codex-wrapper.sh`**: Removed duplicated `_resolve_state_dir()`; replaced disk-based temp files with variable capture for stdout; removed defensive `if [[ -f state.sh ]]` check
- **`cli-detect.sh`**: `detect_cli()` simplified from 6 lines to 1; `detect_all_clis()` replaced with a loop
- **`state.sh`**: `update_agent_stats()` merged two sequential jq calls into one; `check_rate_limit()` reduced from 15 lines to a single compound conditional
- **`enforcement.sh`**: `increment_read_counter()`, `get_read_count()`, `estimate_token_savings()` all condensed
- **`routing.sh`**: Removed 5 redundant `local resolved` declarations; changed `synthesis|*` to `*`
- **`usage.sh`**: Replaced awkward global-variable inner-function pattern with a for-loop using `eval`
- **`session-start.sh`**: Deduplicated 90% shared context string between fresh-session and compaction-recovery paths into a single template
- **`stop.sh`**: Merged if/else grep pattern, inlined one-use constant
- **`pre-compact.sh`**: Removed dead `CONFIG_ESCAPED`/`STATE_ESCAPED` variables that were computed but never used
- **`detect-environment.sh`**: Replaced bash arrays with string concatenation for bash 3 compatibility
- **`run-workflow.sh`**: Extracted `_update_workflow_state()` and `_expand_vars()` helpers, eliminating 3 copy-paste blocks each
- **Agent frontmatter**: Added `capabilities:` list to all 6 agent definitions for better auto-discovery alignment

## [0.2.1] — 2026-02-19

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

## [0.2.0] — 2026-02-12

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

## [0.1.0] — 2026-02-11

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
