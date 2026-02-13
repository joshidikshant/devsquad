#!/usr/bin/env bash
# lib/routing.sh -- Delegation decision tree for DevSquad
# Sourced by skills and hooks. Do not execute directly.
set -euo pipefail

# route_task(task_description) -> JSON { recommended_agent, command, reason }
route_task() {
  local task_desc="$1"
  local agent=""
  local command=""
  local reason=""

  # Lowercase for case-insensitive matching (Bash 3 compatible -- no ${,,})
  local task_lower
  task_lower=$(printf '%s' "$task_desc" | tr '[:upper:]' '[:lower:]')

  # Step 1: Pattern match task description to CATEGORY
  local category=""
  case "$task_lower" in
    *research*|*investigate*|*"find out"*|*"look up"*|*"search for"*)
      category="research"
      ;;
    *"read file"*|*"read the"*|*"analyze file"*|*"understand codebase"*|*"summarize"*|*"review code"*)
      category="reading"
      ;;
    *"write test"*|*"test coverage"*|*"add test"*|*"unit test"*|*"integration test"*)
      category="testing"
      ;;
    *implement*|*refactor*|*"code change"*|*"modify code"*)
      category="development"
      ;;
    *generate*|*boilerplate*|*scaffold*|*"create template"*|*prototype*)
      category="code_generation"
      ;;
    *synthesize*|*decide*|*integrate*|*architect*|*"final review"*)
      category="synthesis"
      ;;
    *)
      category="synthesis"
      ;;
  esac

  # Step 2: Read agent assignment from config.json default_routes
  local project_dir="${CLAUDE_PROJECT_DIR:-.}"
  local config_file="${project_dir}/.devsquad/config.json"
  local config_agent=""
  if command -v jq &>/dev/null && [[ -f "$config_file" ]]; then
    config_agent=$(jq -r --arg cat "$category" '.default_routes[$cat] // empty' "$config_file" 2>/dev/null)
  fi

  # Vocabulary normalization: "claude" -> "self"
  if [[ "$config_agent" == "claude" ]]; then
    config_agent="self"
  fi

  # Step 3: Resolve config value + category to agent name, with hardcoded fallbacks
  case "$category" in
    research)
      local resolved="${config_agent:-gemini}"
      if [[ "$resolved" == "gemini" ]]; then
        agent="gemini-researcher"
        command="@gemini-researcher \"${task_desc}. Under 500 words.\""
        reason="Research task benefits from Gemini's 1M context and web knowledge."
      else
        agent="self"; command=""; reason="Research routed to self per config."
      fi
      ;;
    reading)
      local resolved="${config_agent:-gemini}"
      if [[ "$resolved" == "gemini" ]]; then
        agent="gemini-reader"
        command="@gemini-reader \"Analyze and summarize: ${task_desc}\""
        reason="Large file analysis leverages Gemini's 1M context without consuming Claude's."
      else
        agent="self"; command=""; reason="Reading routed to self per config."
      fi
      ;;
    testing)
      local resolved="${config_agent:-codex}"
      if [[ "$resolved" == "codex" ]]; then
        agent="codex-tester"
        command="@codex-tester \"${task_desc}\""
        reason="Test generation via Codex."
      elif [[ "$resolved" == "gemini" ]]; then
        agent="gemini-tester"
        command="@gemini-tester \"${task_desc}\""
        reason="Test generation with full codebase context via Gemini."
      else
        agent="self"; command=""; reason="Testing routed to self per config."
      fi
      ;;
    development)
      local resolved="${config_agent:-gemini}"
      if [[ "$resolved" == "gemini" ]]; then
        agent="gemini-developer"
        command="@gemini-developer \"${task_desc}\""
        reason="Code changes benefit from Gemini's 1M context for large codebases."
      elif [[ "$resolved" == "codex" ]]; then
        agent="codex-developer"
        command="@codex-developer \"${task_desc}. Under 50 lines.\""
        reason="Code changes via Codex per config."
      else
        agent="self"; command=""; reason="Development routed to self per config."
      fi
      ;;
    code_generation)
      local resolved="${config_agent:-codex}"
      if [[ "$resolved" == "codex" ]]; then
        agent="codex-developer"
        command="@codex-developer \"${task_desc}. Under 50 lines.\""
        reason="Fast code generation via Codex for boilerplate and scaffolding."
      elif [[ "$resolved" == "gemini" ]]; then
        agent="gemini-developer"
        command="@gemini-developer \"${task_desc}\""
        reason="Code generation via Gemini per config."
      else
        agent="self"; command=""; reason="Code generation routed to self per config."
      fi
      ;;
    synthesis|*)
      local resolved="${config_agent:-self}"
      agent="self"
      command=""
      reason="Synthesis and integration tasks require Claude's judgment -- handle directly."
      ;;
  esac

  # Step 4: Track self_calls when routing returns self
  if [[ "$agent" == "self" ]]; then
    local lib_dir
    lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    source "${lib_dir}/state.sh"
    local state_dir="${project_dir}/.devsquad"
    update_agent_stats "$state_dir" "self" "true"
  fi

  # Output JSON
  if command -v jq &>/dev/null; then
    jq -n \
      --arg agent "$agent" \
      --arg command "$command" \
      --arg reason "$reason" \
      '{recommended_agent: $agent, command: $command, reason: $reason}'
  else
    # Manual JSON construction (safe because values don't contain unescaped quotes from case branches)
    local escaped_command
    escaped_command=$(printf '%s' "$command" | sed 's/\\/\\\\/g; s/"/\\"/g')
    local escaped_reason
    escaped_reason=$(printf '%s' "$reason" | sed 's/\\/\\\\/g; s/"/\\"/g')
    printf '{"recommended_agent":"%s","command":"%s","reason":"%s"}\n' "$agent" "$escaped_command" "$escaped_reason"
  fi
}
