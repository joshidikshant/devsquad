---
name: onboarding
description: This skill should be used when the user runs "/devsquad:setup", asks to "configure devsquad", "set up delegation", "change enforcement mode", or "update agent preferences". Guides through environment detection, preference capture, and config generation.
version: 0.1.0
---

# DevSquad Onboarding

## Overview

Onboarding detects the developer's AI tooling environment, captures delegation preferences, and generates a configuration that persists across sessions. This skill can be re-run at any time to update preferences without losing existing configuration.

The onboarding flow consists of five sequential steps: environment detection, preference capture, configuration save, CLAUDE.md snippet generation, and confirmation. Each step builds on the results of the previous one.

## Prerequisites

- The DevSquad plugin must be installed (`.claude-plugin/plugin.json` exists)
- The state management library (`lib/state.sh`) and CLI detection library (`lib/cli-detect.sh`) must be available
- The environment detection skill (`skills/environment-detection/`) should be available for full detection; onboarding degrades gracefully if it is not yet installed

## Onboarding Flow

### Step 1: Environment Detection

Detect the developer's AI tooling environment by invoking the environment detection skill or running its scripts directly.

**Actions:**

1. Determine the plugin root directory. The plugin root is available as `CLAUDE_PLUGIN_ROOT` if running inside a hook, or can be resolved relative to this skill file.

2. Run the detection scripts:
   ```bash
   bash ${CLAUDE_PLUGIN_ROOT}/skills/environment-detection/scripts/detect-environment.sh
   bash ${CLAUDE_PLUGIN_ROOT}/skills/environment-detection/scripts/detect-plugins.sh
   ```

3. If the environment-detection skill is not available, fall back to the core CLI detection library:
   ```bash
   source ${CLAUDE_PLUGIN_ROOT}/lib/cli-detect.sh
   DETECTION_RESULT=$(detect_all_clis)
   ```

4. Parse the JSON results from either detection method to determine:
   - Which CLIs are available (gemini, codex, claude)
   - Their versions if detectable
   - Which plugins are installed (superpowers, gsd, etc.)

5. Present a summary to the developer:
   > "Found: Gemini (available), Codex (available). Missing: none. Plugins: GSD, Superpowers."

   Adjust the summary based on actual detection results. If a CLI is missing, note it clearly:
   > "Found: Gemini (available). Missing: Codex. Plugins: none detected."

### Step 2: Preference Capture

Capture the developer's delegation preferences through an interactive question flow.

**Before asking questions:**

1. Check if `.devsquad/config.json` already exists in the project directory (this indicates a re-run scenario).
2. If it exists, load the current preferences using `lib/state.sh`:
   ```bash
   source ${CLAUDE_PLUGIN_ROOT}/lib/state.sh
   CURRENT_CONFIG=$(read_state "config.json")
   ```
3. Extract current values to use as defaults for each question.

**Question 1: Enforcement Mode**

Present the enforcement mode options:

> **Enforcement Mode**
>
> This controls how aggressively DevSquad enforces delegation rules.
>
> - `advisory` (default): DevSquad suggests delegation but does not block. Claude can override suggestions when it judges direct handling is more efficient.
> - `strict`: DevSquad blocks non-compliant tool use and provides the delegation command to run instead. Claude must follow delegation rules.
>
> Current: [current value or "advisory (default)"]
>
> Which mode? (advisory/strict)

Accept the developer's response. If they provide no answer or an ambiguous response, default to `advisory`.

**Question 2: Default Routes**

Present the default routing table:

> **Default Delegation Routes**
>
> These determine which agent handles each type of task:
>
> | Task Type | Default Route |
> |-----------|--------------|
> | Research / web search | gemini |
> | Large file reading | gemini |
> | Code generation / boilerplate | codex |
> | Testing | codex |
> | Synthesis / integration | claude (self) |
>
> Accept these defaults? Or would you like to customize routes?

If the developer accepts defaults, proceed. If they want customization, ask for each route individually:

> For each task type, specify: gemini, codex, or claude.
> - Research / web search: [current: gemini]
> - Large file reading: [current: gemini]
> - Code generation / boilerplate: [current: codex]
> - Testing: [current: codex]
> - Synthesis / integration: [current: claude] (recommended: always claude)

Only ask for customization if the developer explicitly requests it.

**Question 3: Agent Preferences**

Present the agent configuration options:

> **Agent Preferences**
>
> Fine-tune how DevSquad interacts with external agents:
>
> - `gemini_word_limit`: Maximum words to request in Gemini responses (current: 300)
> - `codex_line_limit`: Maximum lines for Codex outputs (current: 50)
> - `auto_suggest`: Automatically suggest delegation when detecting eligible tasks (current: true)
>
> Accept these defaults or customize?

If customizing, accept numeric values for limits and true/false for auto_suggest. Validate inputs:
- gemini_word_limit: must be a positive integer between 50 and 2000
- codex_line_limit: must be a positive integer between 10 and 500
- auto_suggest: must be true or false

### Step 3: Save Configuration

Persist the captured preferences to disk.

**Before saving**, if enforcement_mode was set to "strict", check for dependency gaps and warn the developer:

1. Check jq availability:
   ```bash
   if ! command -v jq &>/dev/null; then
     echo "Note: Strict mode requires jq for full enforcement. Without jq, hooks fall back to advisory mode."
   fi
   ```

2. Check gemini CLI availability:
   ```bash
   if ! command -v gemini &>/dev/null; then
     echo "Note: Strict mode degraded - gemini CLI not installed. Delegation to Gemini agents will fall back to advisory."
   fi
   ```

3. Check codex CLI availability:
   ```bash
   if ! command -v codex &>/dev/null; then
     echo "Note: Strict mode degraded - codex CLI not installed. Delegation to Codex agents will fall back to advisory."
   fi
   ```

These are informational warnings only -- do not block onboarding. The developer can install missing dependencies later.

**Actions:**

1. Initialize the state directory if it does not exist:
   ```bash
   source ${CLAUDE_PLUGIN_ROOT}/lib/state.sh
   init_state_dir
   ```

2. Build the configuration JSON object with the following schema:
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

3. If this is a re-run, merge new preferences with existing config:
   - Preserve the original `created` timestamp
   - Update the `updated` timestamp to now
   - Only overwrite fields the developer explicitly changed
   - Retain any additional custom fields the developer may have added

4. Write the configuration using atomic write operations:
   ```bash
   write_state "config.json" "$CONFIG_JSON"
   ```

5. Verify the write succeeded by reading back:
   ```bash
   VERIFY=$(read_state "config.json")
   ```

### Step 4: CLAUDE.md Snippet (Opt-in)

Determine whether to generate and insert a CLAUDE.md snippet. This is NOT automatic — it depends on the developer's existing setup, project scope, and explicit consent.

**Actions:**

1. **Evaluate the developer's setup** to formulate a recommendation:

   a. Check if a project CLAUDE.md already exists:
      ```bash
      CLAUDE_MD="${CLAUDE_PROJECT_DIR}/CLAUDE.md"
      HAS_CLAUDE_MD=false
      CLAUDE_MD_LINES=0
      HAS_DEVSQUAD_MARKERS=false
      if [[ -f "$CLAUDE_MD" ]]; then
        HAS_CLAUDE_MD=true
        CLAUDE_MD_LINES=$(wc -l < "$CLAUDE_MD")
        if grep -q '<!-- DEVSQUAD-START -->' "$CLAUDE_MD"; then
          HAS_DEVSQUAD_MARKERS=true
        fi
      fi
      ```

   b. Assess the situation and build a recommendation:
      - **No CLAUDE.md exists**: Recommend yes — "You don't have a CLAUDE.md yet. A DevSquad snippet would give Claude baseline delegation awareness."
      - **Small CLAUDE.md (<50 lines) without markers**: Recommend yes — "Your CLAUDE.md is lightweight. Adding a DevSquad routing section would complement it."
      - **Large CLAUDE.md (50+ lines) without markers**: Recommend caution — "You have a detailed CLAUDE.md (N lines). DevSquad hooks already enforce delegation rules. The snippet would add routing context but you may prefer to manage CLAUDE.md manually."
      - **CLAUDE.md with existing DEVSQUAD markers**: Recommend update — "You already have a DevSquad section. Want to regenerate it with your updated preferences?"

2. **Present the recommendation and ask for consent:**

   > **CLAUDE.md Snippet (Optional)**
   >
   > DevSquad can add a ~60-line section to your CLAUDE.md with:
   > - Identity framing ("I am the Engineering Manager")
   > - Routing table (which agent handles what)
   > - Enforcement context (hooks handle the rest)
   >
   > {Situation-specific recommendation from step 1b}
   >
   > **This is optional.** DevSquad hooks work regardless of whether CLAUDE.md is modified.
   >
   > Add DevSquad section to CLAUDE.md? (yes/no)

3. **If yes:**
   a. Generate the snippet using the generation script:
      ```bash
      SNIPPET=$(bash ${CLAUDE_PLUGIN_ROOT}/skills/onboarding/scripts/generate-claude-md.sh \
        --plugin-root "${CLAUDE_PLUGIN_ROOT}" \
        --project-dir "${CLAUDE_PROJECT_DIR}")
      ```
   b. If the script fails (non-zero exit), warn the developer and fall back to a minimal snippet with just the DEVSQUAD-START/END markers and a note to re-run setup.
   c. Present the generated snippet to the developer for review before insertion.
   d. Ask: "This is what will be added. Proceed? (yes/no)"
   e. If confirmed, run the insertion:
      ```bash
      bash ${CLAUDE_PLUGIN_ROOT}/skills/onboarding/scripts/generate-claude-md.sh \
        --plugin-root "${CLAUDE_PLUGIN_ROOT}" \
        --project-dir "${CLAUDE_PROJECT_DIR}" \
        --insert
      ```
   f. Save `claude_md_managed: true` to config.json (merge into existing config)
   g. Display: "DevSquad section added to CLAUDE.md. Re-run `/devsquad:setup` or `/devsquad:config` to update it."

4. **If no:**
   a. Save `claude_md_managed: false` to config.json (merge into existing config)
   b. Display: "Skipped. DevSquad hooks still enforce delegation rules without CLAUDE.md changes. You can add the snippet later with `/devsquad:setup`."

**On re-run behavior:** If `claude_md_managed` is already true in config.json, default the recommendation to "update" and skip the lengthy evaluation. If false, re-evaluate since the developer's setup may have changed.

### Step 5: Confirmation

Display a summary of everything that was configured.

**Actions:**

1. Present the final summary:

   > **DevSquad Configured Successfully**
   >
   > - Enforcement mode: [advisory/strict]
   > - Available agents: [list of available agents]
   > - Default routes: research -> [route], code generation -> [route], testing -> [route]
   > - Config saved to: `.devsquad/config.json`
   > - CLAUDE.md: [managed by DevSquad / not managed (opt-out)]

2. If any CLI tool was missing during detection, add a reminder:

   > **Note:** The following tools are not installed:
   > - codex: Install with `npm i -g @openai/codex`
   > - gemini: Install with `npm i -g @anthropic/gemini-cli` (or check vendor docs)
   >
   > DevSquad works best with all agents available. You can re-run `/devsquad:setup` after installing them.

3. If no external CLI is available at all, provide a stronger warning:

   > **Warning:** No external AI agents detected. DevSquad cannot delegate work without at least Gemini or Codex installed. All tasks will fall back to Claude (self) until agents are available.

## Re-run Behavior

When `/devsquad:setup` is invoked and `.devsquad/config.json` already exists:

1. Load the existing configuration as the starting point
2. Show "Current value" before each question so the developer knows what is already set
3. Accept empty responses to keep the current value unchanged
4. Only overwrite fields the developer explicitly provides new values for
5. Update the `updated` timestamp but preserve the original `created` timestamp
6. Re-run environment detection to pick up any newly installed tools
7. Offer to regenerate the CLAUDE.md snippet with updated information

This ensures that `/devsquad:setup` is always safe to re-run and never destroys existing customization.

## Error Handling

### jq Not Available

If `jq` is not installed on the system:
- Warn the developer: "jq is not installed. Using basic JSON handling. For best results, install jq."
- Use the jq-optional fallback functions in `lib/state.sh` for JSON operations
- Config will still be created and saved correctly, but complex JSON merging may be less robust

### No External CLI Available

If neither Gemini nor Codex is detected:
- Complete the onboarding flow normally (do not abort)
- Set all routes to "claude" as fallback
- Warn that delegation will not function until at least one external agent is installed
- Save the config so it is ready when agents are installed later

### Detection Script Failure

If environment detection scripts fail or are not yet available:
- Fall back to basic `command -v` checks for gemini, codex, and claude
- Log the detection method used in the config under `environment.detection_method`
- Continue with onboarding using the basic detection results

### General Error Recovery

- Never crash or abort mid-flow. Always complete with a status message.
- If a write operation fails, report the error and suggest checking file permissions
- If a read operation fails on an expected file, treat it as a fresh install scenario
- All error messages should include actionable next steps for the developer
