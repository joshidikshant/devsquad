#!/usr/bin/env bash
# lib/usage.sh -- Usage tracking and zone calculation for DevSquad
# Sourced by hooks and other scripts. Do not execute directly.
set -euo pipefail

# Record an agent invocation to usage files
# Usage: record_usage agent_name prompt_chars response_chars
# Creates/appends to .devsquad/usage/{agent}.json (or .jsonl as fallback)
record_usage() {
  local agent="$1"
  local prompt_len="$2"
  local response_len="$3"

  local project_dir="${CLAUDE_PROJECT_DIR:-.}"
  local usage_dir="${project_dir}/.devsquad/usage"
  local timestamp
  timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

  # Ensure usage directory exists
  mkdir -p "$usage_dir"

  # Create usage record object
  local record
  record="{\"timestamp\":\"${timestamp}\",\"prompt_chars\":${prompt_len},\"response_chars\":${response_len}}"

  if command -v jq &>/dev/null; then
    # jq available: maintain proper JSON array with atomic writes
    local usage_file="${usage_dir}/${agent}.json"
    local temp_file="${usage_file}.tmp.$$"

    if [[ -f "$usage_file" ]]; then
      # Append to existing array
      jq --argjson rec "$record" '. += [$rec]' "$usage_file" > "$temp_file"
    else
      # Create new array
      echo "[$record]" > "$temp_file"
    fi

    mv "$temp_file" "$usage_file"
  else
    # No jq: use JSONL (newline-delimited JSON) as fallback
    local usage_file="${usage_dir}/${agent}.jsonl"
    echo "$record" >> "$usage_file"
  fi
}

# Read Claude's stats cache file for today's metrics
# Returns JSON object with today's token counts and activity, or zeros if unavailable
# Output: {"input_tokens":0,"output_tokens":N,"cache_read_tokens":0,"cache_write_tokens":0,"message_count":N,"tool_call_count":N}
# Note: input_tokens and cache tokens are not available in daily stats (always 0)
read_claude_stats() {
  local stats_file="${HOME}/.claude/stats-cache.json"

  if [[ ! -f "$stats_file" ]]; then
    echo '{"input_tokens":0,"output_tokens":0,"cache_read_tokens":0,"cache_write_tokens":0,"message_count":0,"tool_call_count":0}'
    return
  fi

  local today
  today=$(date +%Y-%m-%d)

  if command -v jq &>/dev/null; then
    # Get today's output tokens (sum all models for today)
    local output_tokens
    output_tokens=$(jq --arg d "$today" '
      [.dailyModelTokens[] | select(.date == $d) | .tokensByModel | to_entries[].value] | add // 0
    ' "$stats_file" 2>/dev/null || echo "0")

    # Get today's activity metrics
    local message_count tool_call_count
    message_count=$(jq --arg d "$today" '
      [.dailyActivity[] | select(.date == $d) | .messageCount] | add // 0
    ' "$stats_file" 2>/dev/null || echo "0")
    tool_call_count=$(jq --arg d "$today" '
      [.dailyActivity[] | select(.date == $d) | .toolCallCount] | add // 0
    ' "$stats_file" 2>/dev/null || echo "0")

    echo "{\"input_tokens\":0,\"output_tokens\":${output_tokens},\"cache_read_tokens\":0,\"cache_write_tokens\":0,\"message_count\":${message_count},\"tool_call_count\":${tool_call_count}}"
  else
    # No jq: use grep+awk to extract today's values
    # Find today's section in dailyModelTokens and sum token values
    local output_tokens=0
    local section
    section=$(awk -v d="$today" '
      $0 ~ "\"date\".*\"" d "\"" { found=1; next }
      found && /tokensByModel/ { in_tokens=1; next }
      found && in_tokens && /[0-9]+/ { gsub(/[^0-9]/, ""); if($0+0 > 0) sum+=$0+0 }
      found && /\}/ && in_tokens { in_tokens=0 }
      found && /\}/ && !in_tokens { found=0 }
      END { print sum+0 }
    ' "$stats_file" 2>/dev/null)
    output_tokens="${section:-0}"

    # Get message count and tool call count from dailyActivity for today
    local message_count=0
    local tool_call_count=0
    local activity_section
    activity_section=$(awk -v d="$today" '
      $0 ~ "\"date\".*\"" d "\"" && /messageCount/ {
        gsub(/.*messageCount.*:/, ""); gsub(/[^0-9]/, ""); mc=$0+0
        getline; getline; gsub(/.*toolCallCount.*:/, ""); gsub(/[^0-9]/, ""); tc=$0+0
        print mc, tc; exit
      }
    ' "$stats_file" 2>/dev/null)
    if [[ -n "$activity_section" ]]; then
      message_count=$(echo "$activity_section" | awk '{print $1}')
      tool_call_count=$(echo "$activity_section" | awk '{print $2}')
    fi

    echo "{\"input_tokens\":0,\"output_tokens\":${output_tokens},\"cache_read_tokens\":0,\"cache_write_tokens\":0,\"message_count\":${message_count:-0},\"tool_call_count\":${tool_call_count:-0}}"
  fi
}

# Calculate budget zone based on daily output token volume
# Usage: calculate_zone input_tokens output_tokens
# Note: input_tokens is unused (not available in stats-cache.json)
# Zone thresholds based on daily output volume:
#   green: < 100K output tokens today
#   yellow: 100K-200K output tokens today
#   red: > 200K output tokens today
calculate_zone() {
  local input_tokens="${1:-0}"
  local output_tokens="${2:-0}"

  # Zone based on daily output token volume
  # These thresholds reflect typical daily usage intensity:
  # - green: light usage day (<100K output tokens)
  # - yellow: moderate-heavy usage (100K-200K output tokens)
  # - red: very heavy usage (>200K output tokens)
  if [[ $output_tokens -lt 100000 ]]; then
    echo "green"
  elif [[ $output_tokens -lt 200000 ]]; then
    echo "yellow"
  else
    echo "red"
  fi
}

# Get comprehensive usage summary
# Returns JSON summary of Claude, Gemini, and Codex usage
get_usage_summary() {
  local project_dir="${CLAUDE_PROJECT_DIR:-.}"
  local usage_dir="${project_dir}/.devsquad/usage"

  # Read Claude stats
  local claude_stats
  claude_stats=$(read_claude_stats)

  if command -v jq &>/dev/null; then
    # Extract Claude token counts and activity metrics
    local input_tokens output_tokens message_count tool_call_count
    input_tokens=$(echo "$claude_stats" | jq -r '.input_tokens // 0')
    output_tokens=$(echo "$claude_stats" | jq -r '.output_tokens // 0')
    message_count=$(echo "$claude_stats" | jq -r '.message_count // 0')
    tool_call_count=$(echo "$claude_stats" | jq -r '.tool_call_count // 0')

    # Calculate zone
    local zone
    zone=$(calculate_zone "$input_tokens" "$output_tokens")

    # Count Gemini invocations and sum response chars
    local gemini_count=0
    local gemini_total=0
    if [[ -f "${usage_dir}/gemini.json" ]]; then
      gemini_count=$(jq 'length' "${usage_dir}/gemini.json" 2>/dev/null || echo "0")
      gemini_total=$(jq '[.[].response_chars] | add // 0' "${usage_dir}/gemini.json" 2>/dev/null || echo "0")
    elif [[ -f "${usage_dir}/gemini.jsonl" ]]; then
      gemini_count=$(wc -l < "${usage_dir}/gemini.jsonl" 2>/dev/null | tr -d ' ' || echo "0")
      gemini_total=$(awk -F'[,:}]' '{for(i=1;i<=NF;i++){if($i~/response_chars/){print $(i+1)}}}' "${usage_dir}/gemini.jsonl" 2>/dev/null | awk '{s+=$1}END{print s+0}')
    fi

    # Count Codex invocations and sum response chars
    local codex_count=0
    local codex_total=0
    if [[ -f "${usage_dir}/codex.json" ]]; then
      codex_count=$(jq 'length' "${usage_dir}/codex.json" 2>/dev/null || echo "0")
      codex_total=$(jq '[.[].response_chars] | add // 0' "${usage_dir}/codex.json" 2>/dev/null || echo "0")
    elif [[ -f "${usage_dir}/codex.jsonl" ]]; then
      codex_count=$(wc -l < "${usage_dir}/codex.jsonl" 2>/dev/null | tr -d ' ' || echo "0")
      codex_total=$(awk -F'[,:}]' '{for(i=1;i<=NF;i++){if($i~/response_chars/){print $(i+1)}}}' "${usage_dir}/codex.jsonl" 2>/dev/null | awk '{s+=$1}END{print s+0}')
    fi

    # Build JSON summary
    cat <<USAGE_JSON
{
  "claude": {
    "input_tokens": ${input_tokens},
    "output_tokens": ${output_tokens},
    "message_count": ${message_count},
    "tool_call_count": ${tool_call_count},
    "zone": "${zone}"
  },
  "gemini": {
    "invocations": ${gemini_count},
    "total_response_chars": ${gemini_total}
  },
  "codex": {
    "invocations": ${codex_count},
    "total_response_chars": ${codex_total}
  }
}
USAGE_JSON
  else
    # No jq: return simplified text format
    local input_tokens output_tokens
    input_tokens=$(echo "$claude_stats" | grep -o '"input_tokens":[0-9]*' | grep -o '[0-9]*$' || echo "0")
    output_tokens=$(echo "$claude_stats" | grep -o '"output_tokens":[0-9]*' | grep -o '[0-9]*$' || echo "0")

    local zone
    zone=$(calculate_zone "$input_tokens" "$output_tokens")

    # Count lines for invocations (simplified)
    local gemini_count=0
    local codex_count=0
    if [[ -f "${usage_dir}/gemini.jsonl" ]]; then
      gemini_count=$(wc -l < "${usage_dir}/gemini.jsonl" 2>/dev/null | tr -d ' ' || echo "0")
    fi
    if [[ -f "${usage_dir}/codex.jsonl" ]]; then
      codex_count=$(wc -l < "${usage_dir}/codex.jsonl" 2>/dev/null | tr -d ' ' || echo "0")
    fi

    cat <<USAGE_TEXT
Claude: ${input_tokens} input, ${output_tokens} output tokens (zone: ${zone})
Gemini: ${gemini_count} invocations
Codex: ${codex_count} invocations
USAGE_TEXT
  fi
}

# Reset usage tracking for new session
# Creates session start marker without deleting historical data
reset_usage_session() {
  local project_dir="${CLAUDE_PROJECT_DIR:-.}"
  local usage_dir="${project_dir}/.devsquad/usage"
  local timestamp
  timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

  mkdir -p "$usage_dir"
  echo "$timestamp" > "${usage_dir}/session_start"
}
